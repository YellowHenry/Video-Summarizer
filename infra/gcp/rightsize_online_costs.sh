#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/load_python_env.sh"

gcloud() {
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy gcloud "$@"
}

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${SQL_TIER:=db-f1-micro}"
: "${SQL_TIER_FALLBACK:=db-g1-small}"
: "${SQL_STORAGE_TYPE:=}"

gcloud config set project "${PROJECT_ID}" >/dev/null

CURRENT_TIER="$(gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" --project "${PROJECT_ID}" --format='value(settings.tier)' 2>/dev/null || true)"
CURRENT_STORAGE="$(gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" --project "${PROJECT_ID}" --format='value(settings.dataDiskType)' 2>/dev/null || true)"
echo "[rightsize] Current Cloud SQL tier=${CURRENT_TIER:-unknown} storage=${CURRENT_STORAGE:-unknown}"
echo "[rightsize] Target tier=${SQL_TIER} fallback=${SQL_TIER_FALLBACK} storage_override=${SQL_STORAGE_TYPE:-<none>}"

patch_sql() {
  local tier="$1"
  local args=(
    sql instances patch "${CLOUD_SQL_INSTANCE}"
    --project "${PROJECT_ID}"
    --tier="${tier}"
    --quiet
  )
  if [[ -n "${SQL_STORAGE_TYPE}" ]]; then
    args+=(--storage-type="${SQL_STORAGE_TYPE}")
  fi
  gcloud "${args[@]}"
}

current_sql_tier() {
  gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" \
    --project "${PROJECT_ID}" \
    --format='value(settings.tier)' 2>/dev/null || true
}

current_running_update_op() {
  gcloud sql operations list \
    --instance "${CLOUD_SQL_INSTANCE}" \
    --project "${PROJECT_ID}" \
    --filter='status=RUNNING AND operationType=UPDATE' \
    --limit 1 \
    --format='value(name)' 2>/dev/null || true
}

wait_for_running_update_clear() {
  local attempts=60
  local delay_seconds=10
  local op=""
  for ((i=1; i<=attempts; i++)); do
    op="$(current_running_update_op)"
    if [[ -z "${op}" ]]; then
      return 0
    fi
    echo "[rightsize] Waiting for in-progress SQL update operation to finish (${op}, attempt ${i}/${attempts})..."
    sleep "${delay_seconds}"
  done
  return 1
}

APPLIED_TIER=""
if patch_sql "${SQL_TIER}"; then
  APPLIED_TIER="${SQL_TIER}"
  echo "[rightsize] Applied primary SQL tier: ${SQL_TIER}"
else
  CURRENT_AFTER_PRIMARY="$(current_sql_tier)"
  if [[ "${CURRENT_AFTER_PRIMARY}" == "${SQL_TIER}" ]]; then
    APPLIED_TIER="${SQL_TIER}"
    echo "[rightsize] Primary patch returned non-zero, but instance tier is already '${SQL_TIER}'. Treating as success."
  else
    RUNNING_OP="$(current_running_update_op)"
    if [[ -n "${RUNNING_OP}" ]]; then
      echo "[rightsize] Primary patch may still be applying (running op: ${RUNNING_OP})."
      if wait_for_running_update_clear; then
        CURRENT_AFTER_WAIT="$(current_sql_tier)"
        if [[ "${CURRENT_AFTER_WAIT}" == "${SQL_TIER}" ]]; then
          APPLIED_TIER="${SQL_TIER}"
          echo "[rightsize] Primary SQL tier '${SQL_TIER}' applied after wait."
        fi
      fi
    fi
  fi

  if [[ -z "${APPLIED_TIER}" ]]; then
    if [[ "${SQL_TIER_FALLBACK}" == "${SQL_TIER}" ]]; then
      echo "[rightsize] Primary tier failed and no distinct fallback was configured." >&2
      exit 1
    fi
    echo "[rightsize] Primary tier '${SQL_TIER}' failed. Trying fallback '${SQL_TIER_FALLBACK}'..."
    patch_sql "${SQL_TIER_FALLBACK}"
    APPLIED_TIER="${SQL_TIER_FALLBACK}"
    echo "[rightsize] Applied fallback SQL tier: ${SQL_TIER_FALLBACK}"
  fi
fi

echo "[rightsize] Final Cloud SQL settings:"
gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" \
  --project "${PROJECT_ID}" \
  --format='table(name,region,state,settings.activationPolicy,settings.tier,settings.dataDiskType,settings.dataDiskSizeGb)'

echo "[rightsize] Complete (applied tier=${APPLIED_TIER})."
