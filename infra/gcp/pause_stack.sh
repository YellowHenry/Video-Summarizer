#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/load_python_env.sh"

gcloud() {
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy gcloud "$@"
}

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${WORKER_RUNTIME:=compute_engine}"
: "${WORKER_SERVICE:=audio-summarizer-worker}"
: "${WORKER_VM_NAME:=audio-summarizer-worker-vm}"
: "${WORKER_VM_ZONE:=us-central1-a}"

log() {
  echo "[pause] $*"
}

if [[ "${WORKER_RUNTIME}" != "compute_engine" ]]; then
  echo "[pause] WORKER_RUNTIME=${WORKER_RUNTIME} is not supported. Set WORKER_RUNTIME=compute_engine." >&2
  exit 1
fi

gcloud config set project "${PROJECT_ID}" >/dev/null

if gcloud run services describe "${WORKER_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" >/dev/null 2>&1; then
  gcloud run services update "${WORKER_SERVICE}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --min-instances 0 \
    >/dev/null
  log "Cloud Run worker '${WORKER_SERVICE}' set to min-instances=0."
else
  log "Cloud Run worker '${WORKER_SERVICE}' not found; skipping Cloud Run scale-down."
fi

if gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  vm_status="$(gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --project "${PROJECT_ID}" --format='value(status)')"
  if [[ "${vm_status}" == "RUNNING" ]]; then
    gcloud compute instances stop "${WORKER_VM_NAME}" \
      --zone "${WORKER_VM_ZONE}" \
      --project "${PROJECT_ID}" \
      --quiet \
      >/dev/null
    log "Worker VM '${WORKER_VM_NAME}' stopped."
  else
    log "Worker VM '${WORKER_VM_NAME}' already ${vm_status}."
  fi
else
  log "Worker VM '${WORKER_VM_NAME}' not found; skipping VM stop."
fi

if gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  sql_policy="$(gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" --project "${PROJECT_ID}" --format='value(settings.activationPolicy)' | tr '[:lower:]' '[:upper:]')"
  if [[ "${sql_policy}" != "NEVER" ]]; then
    gcloud sql instances patch "${CLOUD_SQL_INSTANCE}" \
      --project "${PROJECT_ID}" \
      --activation-policy=NEVER \
      --quiet \
      >/dev/null
    log "Cloud SQL '${CLOUD_SQL_INSTANCE}' set to activation policy NEVER."
  else
    log "Cloud SQL '${CLOUD_SQL_INSTANCE}' already set to activation policy NEVER."
  fi
else
  log "Cloud SQL instance '${CLOUD_SQL_INSTANCE}' not found; skipping SQL pause."
fi

log "Pause complete."
