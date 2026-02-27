#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/load_python_env.sh"

gcloud() {
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy gcloud "$@"
}

resolve_openai_api_key() {
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    return
  fi
  if ! gcp_has_python; then
    return
  fi
  local key=""
  key="$(gcp_run_python "${SCRIPT_DIR}/read_openai_key.py" 2>/dev/null || true)"
  if [[ -n "${key}" ]]; then
    export OPENAI_API_KEY="${key}"
  fi
}

preflight_checks() {
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "Preflight failed: gcloud is required in PATH." >&2
    exit 1
  fi
  if ! command -v docker >/dev/null 2>&1; then
    echo "Preflight failed: docker is required in PATH." >&2
    exit 1
  fi

  if command -v timeout >/dev/null 2>&1; then
    if ! timeout 25 docker info >/dev/null 2>&1; then
      cat >&2 <<EOF
Preflight failed: docker daemon is unavailable or unresponsive.
Fix:
  - Start/restart Docker Desktop
  - In WSL mode, run: wsl --shutdown (PowerShell), then reopen Docker Desktop
EOF
      exit 1
    fi
  else
    if ! docker info >/dev/null 2>&1; then
      cat >&2 <<EOF
Preflight failed: docker daemon is unavailable.
Fix:
  - Start/restart Docker Desktop
EOF
      exit 1
    fi
  fi

  local win_mount=""
  if [[ -d "/mnt/c" ]]; then
    win_mount="/mnt/c"
  elif [[ -d "/c" ]]; then
    win_mount="/c"
  fi
  if [[ -n "${win_mount}" ]]; then
    local avail_kb=""
    avail_kb="$(df -Pk "${win_mount}" | awk 'NR==2 {print $4}' || true)"
    if [[ -n "${avail_kb}" ]] && [[ "${avail_kb}" =~ ^[0-9]+$ ]] && (( avail_kb < 2097152 )); then
      local avail_mb=$((avail_kb / 1024))
      cat >&2 <<EOF
Preflight failed: low disk space on Windows drive (${win_mount}) - about ${avail_mb} MB free.
This commonly causes Docker build/push crashes (including BuildKit SIGBUS/fault errors).
Free up at least 5-10 GB on C: and rerun deploy.
EOF
      exit 1
    fi
  fi

  # Set project up front so all subsequent gcloud calls target the same project.
  gcloud config set project "${PROJECT_ID}" >/dev/null

  local active_account=""
  active_account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n1 || true)"
  if [[ -z "${active_account}" ]]; then
    cat >&2 <<EOF
Preflight failed: no active gcloud account is selected.
Fix:
  gcloud auth login
  gcloud config set project ${PROJECT_ID}
EOF
    exit 1
  fi

  local billing_enabled=""
  if ! billing_enabled="$(gcloud billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)' 2>/dev/null)"; then
    cat >&2 <<EOF
Preflight failed: unable to verify billing for project '${PROJECT_ID}'.
Run:
  gcloud billing projects describe ${PROJECT_ID}
If billing is not linked:
  gcloud billing accounts list
  gcloud billing projects link ${PROJECT_ID} --billing-account=XXXXXX-XXXXXX-XXXXXX
EOF
    exit 1
  fi

  if [[ "${billing_enabled}" != "True" && "${billing_enabled}" != "true" ]]; then
    cat >&2 <<EOF
Preflight failed: billing is not enabled for project '${PROJECT_ID}'.
Run:
  gcloud billing accounts list
  gcloud billing projects link ${PROJECT_ID} --billing-account=XXXXXX-XXXXXX-XXXXXX
Then rerun deploy.
EOF
    exit 1
  fi

  echo "Preflight checks passed (account=${active_account}, project=${PROJECT_ID}, billing=${billing_enabled})."
}

resolve_openai_api_key

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${REPO:?REPO is required}"
: "${IMAGE_API:?IMAGE_API is required}"
: "${WORKER_RUNTIME:=compute_engine}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${REDIS_RUNTIME:=memorystore}"
: "${BUCKET_NAME:?BUCKET_NAME is required}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY is required (or set backend/config.py OPENAI_API_KEY)}"

if [[ "${REDIS_RUNTIME}" != "worker_vm" ]]; then
  : "${REDIS_INSTANCE:?REDIS_INSTANCE is required when REDIS_RUNTIME=${REDIS_RUNTIME}}"
fi

if [[ "${WORKER_RUNTIME}" != "compute_engine" ]]; then
  echo "WORKER_RUNTIME=${WORKER_RUNTIME} is not supported by this repo anymore." >&2
  echo "Set WORKER_RUNTIME=compute_engine in infra/gcp/deploy_config.py and rerun." >&2
  exit 1
fi

preflight_checks

echo "[1/6] Bootstrap infrastructure..."
bash "${SCRIPT_DIR}/bootstrap.sh"

echo "[2/6] Build and push API/web images (worker runs on VM)..."
if [[ -n "${IMAGE_WEB:-}" ]]; then
  BUILD_TARGETS="api,web" bash "${SCRIPT_DIR}/build_and_push.sh"
else
  BUILD_TARGETS="api" bash "${SCRIPT_DIR}/build_and_push.sh"
fi

echo "[3/6] Deploy API..."
BUILD_AND_PUSH_API=false bash "${SCRIPT_DIR}/deploy_api.sh"

echo "[4/6] Provision persistent worker VM..."
bash "${SCRIPT_DIR}/provision_worker_vm.sh"

echo "[5/6] Deploy worker code/env to VM..."
bash "${SCRIPT_DIR}/deploy_worker_vm.sh"

if [[ -n "${IMAGE_WEB:-}" ]]; then
  echo "[6/6] Deploy web frontend..."
  bash "${SCRIPT_DIR}/deploy_web.sh"
else
  echo "[6/6] Skipping web deploy (set IMAGE_WEB to enable)."
fi

if [[ -n "${API_DOMAIN:-}" ]]; then
  SERVICE="${API_SERVICE:-audio-summarizer-api}" DOMAIN="${API_DOMAIN}" REGION="${REGION}" \
    bash "${SCRIPT_DIR}/map_domain.sh"
fi

if [[ -n "${WEB_DOMAIN:-}" ]]; then
  SERVICE="${WEB_SERVICE:-audio-summarizer-web}" DOMAIN="${WEB_DOMAIN}" REGION="${REGION}" \
    bash "${SCRIPT_DIR}/map_domain.sh"
fi

if [[ "${RUN_VALIDATE_DEPLOY:-false}" == "true" ]]; then
  echo "[Validation] Running deploy validation checks..."
  bash "${SCRIPT_DIR}/validate_deploy.sh"
fi

echo "Deployment workflow complete."
