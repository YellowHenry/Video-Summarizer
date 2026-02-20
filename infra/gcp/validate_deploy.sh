#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/load_python_env.sh"

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${API_SERVICE:=audio-summarizer-api}"
: "${WORKER_RUNTIME:=compute_engine}"
: "${WORKER_VM_NAME:=audio-summarizer-worker-vm}"
: "${WORKER_VM_ZONE:=us-central1-a}"
: "${WEB_SERVICE:=audio-summarizer-web}"
: "${SMOKE_TIMEOUT_SECONDS:=1200}"
: "${SMOKE_POLL_SECONDS:=10}"

if [[ "${WORKER_RUNTIME}" != "compute_engine" ]]; then
  echo "WORKER_RUNTIME=${WORKER_RUNTIME} is not supported. Set WORKER_RUNTIME=compute_engine." >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is required in PATH" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required in PATH" >&2
  exit 1
fi

if ! gcp_has_python; then
  echo "python/python3/py is required in PATH" >&2
  exit 1
fi

api_url="$(gcloud run services describe "${API_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
if [[ -z "${api_url}" ]]; then
  echo "Could not resolve API service URL for ${API_SERVICE}" >&2
  exit 1
fi

web_url=""
if gcloud run services describe "${WEB_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" >/dev/null 2>&1; then
  web_url="$(gcloud run services describe "${WEB_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
fi

echo "API URL: ${api_url}"
if [[ -n "${web_url}" ]]; then
  echo "Web URL: ${web_url}"
else
  echo "Web service '${WEB_SERVICE}' not found; skipping web URL checks."
fi

health_payload="$(curl -fsS "${api_url}/healthz")"
gcp_run_python - "${health_payload}" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("ok") is not True:
    raise SystemExit("Health check failed: /healthz did not return {\"ok\": true}")
print("Health check OK")
PY

if [[ -n "${web_url}" ]]; then
  http_code="$(curl -sS -o /dev/null -w '%{http_code}' "${web_url}")"
  if [[ "${http_code}" != "200" ]]; then
    echo "Web service returned HTTP ${http_code}" >&2
    exit 1
  fi
  echo "Web URL check OK"
fi

if ! gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Worker VM '${WORKER_VM_NAME}' not found in zone '${WORKER_VM_ZONE}'" >&2
  exit 1
fi
vm_worker_state="$(gcloud compute ssh "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --project "${PROJECT_ID}" --command "systemctl is-active capstone-worker || true" 2>/dev/null | tr -d '\r')"
if [[ "${vm_worker_state}" != "active" ]]; then
  echo "VM worker service is not active (state='${vm_worker_state}')." >&2
  exit 1
fi
echo "VM worker service check OK"

if [[ -n "${SMOKE_YOUTUBE_URL:-}" ]]; then
  echo "Submitting smoke job..."
  create_payload="$(gcp_run_python - <<'PY'
import json
import os
print(json.dumps({
    "youtube_url": os.environ["SMOKE_YOUTUBE_URL"],
    "prefer_youtube_captions": True,
}))
PY
)"
  job_response="$(curl -fsS -X POST "${api_url}/api/jobs" -H "Content-Type: application/json" -d "${create_payload}")"
  job_id="$(gcp_run_python - "${job_response}" <<'PY'
import json
import sys
print(json.loads(sys.argv[1])["job_id"])
PY
)"
  echo "Smoke job id: ${job_id}"

  deadline=$(( $(date +%s) + SMOKE_TIMEOUT_SECONDS ))
  while true; do
    now="$(date +%s)"
    if (( now > deadline )); then
      echo "Smoke job timed out after ${SMOKE_TIMEOUT_SECONDS}s" >&2
      exit 1
    fi

    detail="$(curl -fsS "${api_url}/api/jobs/${job_id}")"
    status="$(gcp_run_python - "${detail}" <<'PY'
import json
import sys
print(json.loads(sys.argv[1])["status"])
PY
)"
    echo "Smoke job status: ${status}"

    if [[ "${status}" == "complete" ]]; then
      break
    fi
    if [[ "${status}" == "failed" ]]; then
      error_text="$(gcp_run_python - "${detail}" <<'PY'
import json
import sys
print(json.loads(sys.argv[1]).get("error") or "")
PY
)"
      echo "Smoke job failed: ${error_text}" >&2
      exit 1
    fi
    sleep "${SMOKE_POLL_SECONDS}"
  done

  summary="$(curl -fsS "${api_url}/api/jobs/${job_id}/summary")"
  transcript="$(curl -fsS "${api_url}/api/jobs/${job_id}/transcript")"
  gcp_run_python - "${summary}" "${transcript}" <<'PY'
import json
import sys

summary = json.loads(sys.argv[1]).get("text", "").strip()
transcript = json.loads(sys.argv[2]).get("text", "").strip()
if not summary:
    raise SystemExit("Smoke summary is empty")
if not transcript:
    raise SystemExit("Smoke transcript is empty")
print("Smoke summary/transcript retrieval OK")
PY
fi

if [[ -n "${SEARCH_QUESTION:-}" ]]; then
  search_payload="$(gcp_run_python - <<'PY'
import json
import os
print(json.dumps({"question": os.environ["SEARCH_QUESTION"], "top_k": 5}))
PY
)"
  search_response="$(curl -fsS -X POST "${api_url}/api/search" -H "Content-Type: application/json" -d "${search_payload}")"
  gcp_run_python - "${search_response}" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
answer = (payload.get("answer") or "").strip()
if not answer:
    raise SystemExit("Search answer is empty")
print(f"Search check OK (hits={len(payload.get('hits', []))})")
PY
fi

echo "Deployment validation complete."
