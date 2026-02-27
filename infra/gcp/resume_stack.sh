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
: "${WORKER_VM_NAME:=audio-summarizer-worker-vm}"
: "${WORKER_VM_ZONE:=us-central1-a}"

log() {
  echo "[resume] $*"
}

warn() {
  echo "[resume] Warning: $*" >&2
}

if [[ "${WORKER_RUNTIME}" != "compute_engine" ]]; then
  echo "[resume] WORKER_RUNTIME=${WORKER_RUNTIME} is not supported. Set WORKER_RUNTIME=compute_engine." >&2
  exit 1
fi

wait_for_sql_runnable() {
  local attempts=30
  local delay_seconds=10
  local state=""
  for ((i=1; i<=attempts; i++)); do
    state="$(gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" --project "${PROJECT_ID}" --format='value(state)' 2>/dev/null || true)"
    if [[ "${state}" == "RUNNABLE" ]]; then
      log "Cloud SQL '${CLOUD_SQL_INSTANCE}' is RUNNABLE."
      return 0
    fi
    log "Waiting for Cloud SQL '${CLOUD_SQL_INSTANCE}' to become RUNNABLE (current=${state:-unknown}, attempt ${i}/${attempts})..."
    sleep "${delay_seconds}"
  done
  echo "[resume] Cloud SQL '${CLOUD_SQL_INSTANCE}' did not become RUNNABLE in time (last state=${state:-unknown})." >&2
  return 1
}

wait_for_vm_running() {
  local attempts=18
  local delay_seconds=10
  local state=""
  for ((i=1; i<=attempts; i++)); do
    state="$(gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --project "${PROJECT_ID}" --format='value(status)' 2>/dev/null || true)"
    if [[ "${state}" == "RUNNING" ]]; then
      log "Worker VM '${WORKER_VM_NAME}' is RUNNING."
      return 0
    fi
    log "Waiting for worker VM '${WORKER_VM_NAME}' to become RUNNING (current=${state:-unknown}, attempt ${i}/${attempts})..."
    sleep "${delay_seconds}"
  done
  echo "[resume] Worker VM '${WORKER_VM_NAME}' did not become RUNNING in time (last state=${state:-unknown})." >&2
  return 1
}

gcloud config set project "${PROJECT_ID}" >/dev/null

if gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  sql_policy="$(gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" --project "${PROJECT_ID}" --format='value(settings.activationPolicy)' | tr '[:lower:]' '[:upper:]')"
  if [[ "${sql_policy}" != "ALWAYS" ]]; then
    gcloud sql instances patch "${CLOUD_SQL_INSTANCE}" \
      --project "${PROJECT_ID}" \
      --activation-policy=ALWAYS \
      --quiet \
      >/dev/null
    log "Cloud SQL '${CLOUD_SQL_INSTANCE}' set to activation policy ALWAYS."
  else
    log "Cloud SQL '${CLOUD_SQL_INSTANCE}' already set to activation policy ALWAYS."
  fi
  wait_for_sql_runnable
else
  warn "Cloud SQL instance '${CLOUD_SQL_INSTANCE}' not found; skipping SQL resume."
fi

if gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  vm_status="$(gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --project "${PROJECT_ID}" --format='value(status)')"
  if [[ "${vm_status}" != "RUNNING" ]]; then
    gcloud compute instances start "${WORKER_VM_NAME}" \
      --zone "${WORKER_VM_ZONE}" \
      --project "${PROJECT_ID}" \
      --quiet \
      >/dev/null
    log "Worker VM '${WORKER_VM_NAME}' start requested."
  else
    log "Worker VM '${WORKER_VM_NAME}' already RUNNING."
  fi
  wait_for_vm_running

  if ! gcloud compute ssh "${WORKER_VM_NAME}" \
    --zone "${WORKER_VM_ZONE}" \
    --project "${PROJECT_ID}" \
    --command "systemctl is-active cloud-sql-proxy && systemctl is-active capstone-worker" \
    >/dev/null 2>&1; then
    warn "Worker services check failed. Run: gcloud compute ssh ${WORKER_VM_NAME} --zone ${WORKER_VM_ZONE} --command \"sudo systemctl status cloud-sql-proxy capstone-worker --no-pager\""
  else
    log "Worker services are active on VM (${WORKER_VM_NAME})."
  fi
else
  warn "Worker VM '${WORKER_VM_NAME}' not found; skipping VM start."
fi

log "Resume complete."
