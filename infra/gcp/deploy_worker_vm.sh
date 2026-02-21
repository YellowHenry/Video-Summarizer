#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/load_python_env.sh"

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

resolve_openai_api_key

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${REDIS_INSTANCE:?REDIS_INSTANCE is required}"
: "${BUCKET_NAME:?BUCKET_NAME is required}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY is required (or set backend/config.py OPENAI_API_KEY)}"
: "${OPENAI_TRUST_ENV_PROXY:=false}"
: "${WORKER_VM_NAME:=audio-summarizer-worker-vm}"
: "${WORKER_VM_ZONE:=us-central1-a}"
: "${WORKER_VM_USER:=worker}"
: "${WORKER_SERVICE:=audio-summarizer-worker}"
: "${DISABLE_CLOUD_RUN_WORKER:=true}"
: "${CLOUD_RUN_SHADOW_QUEUE:=cloudrun-disabled}"
: "${WEBAPP_ENABLE_PGVECTOR:=true}"
: "${RQ_RETRY_MAX:=3}"
: "${RQ_RETRY_INTERVALS:=30,120,300}"
: "${YTDLP_STRICT_COOKIES:=true}"
: "${YOUTUBE_TRANSCRIPT_API_FALLBACK:=false}"
: "${YTDLP_COOKIES_FROM_BROWSER:=chrome}"
: "${YTDLP_COOKIES_FROM_BROWSER_PROFILE:=Default}"
: "${YTDLP_JS_RUNTIMES:=node}"
: "${YTDLP_REMOTE_COMPONENTS:=}"
: "${PROXY_ENABLED:=false}"
: "${PROXY_CAPTIONS_ONLY:=false}"
: "${PROXY_ROTATION_MODE:=on_rate_limit}"
: "${PROXY_MAX_RETRIES:=3}"
: "${PROXY_BACKOFF_SECONDS:=2}"
: "${NO_PROXY:=169.254.169.254,metadata.google.internal,localhost,127.0.0.1}"
: "${PROXY_POOL:=}"
: "${PROXY_AUTOGENERATE:=false}"
: "${PROXY_AUTOGENERATE_TEMPLATE:=}"
: "${PROXY_AUTOGENERATE_START:=1}"
: "${PROXY_AUTOGENERATE_END:=1}"

if [[ "${PROXY_ENABLED,,}" == "true" ]]; then
  if [[ -z "${HTTP_PROXY:-}" && -z "${HTTPS_PROXY:-}" && -z "${ALL_PROXY:-}" && -z "${PROXY_POOL:-}" && ! ( "${PROXY_AUTOGENERATE,,}" == "true" && -n "${PROXY_AUTOGENERATE_TEMPLATE:-}" ) ]]; then
    echo "PROXY_ENABLED=true but no proxy endpoints were configured." >&2
    echo "Set HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / PROXY_POOL, or set PROXY_AUTOGENERATE=true with PROXY_AUTOGENERATE_TEMPLATE." >&2
    exit 1
  fi
fi

if ! gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" >/dev/null 2>&1; then
  echo "Worker VM '${WORKER_VM_NAME}' not found in zone '${WORKER_VM_ZONE}'." >&2
  echo "Run: bash infra/gcp/provision_worker_vm.sh" >&2
  exit 1
fi

INSTANCE_CONN="${PROJECT_ID}:${REGION}:${CLOUD_SQL_INSTANCE}"
REDIS_HOST="$(gcloud redis instances describe "${REDIS_INSTANCE}" --region "${REGION}" --format='value(host)')"

SRC_TAR="$(mktemp /tmp/capstone-worker-src.XXXXXX.tgz)"
ENV_FILE="$(mktemp /tmp/capstone-worker-env.XXXXXX)"
REMOTE_TAR="/tmp/capstone-worker-src.tgz"
REMOTE_ENV="/tmp/capstone-worker.env"

cleanup() {
  rm -f "${SRC_TAR}" "${ENV_FILE}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

tar \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude="*.pyo" \
  --exclude=".pytest_cache" \
  -czf "${SRC_TAR}" \
  backend \
  requirements.txt

cat >"${ENV_FILE}" <<EOF
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${INSTANCE_CONN}
REDIS_URL=redis://${REDIS_HOST}:6379/0
OBJECT_STORAGE_BACKEND=gcs
GCS_BUCKET=${BUCKET_NAME}
OPENAI_API_KEY=${OPENAI_API_KEY}
OPENAI_TRUST_ENV_PROXY=${OPENAI_TRUST_ENV_PROXY}
RQ_QUEUE_NAME=jobs
RQ_RETRY_MAX=${RQ_RETRY_MAX}
RQ_RETRY_INTERVALS=${RQ_RETRY_INTERVALS}
WEBAPP_ENABLE_PGVECTOR=${WEBAPP_ENABLE_PGVECTOR}
YTDLP_STRICT_COOKIES=${YTDLP_STRICT_COOKIES}
YOUTUBE_TRANSCRIPT_API_FALLBACK=${YOUTUBE_TRANSCRIPT_API_FALLBACK}
YTDLP_COOKIES_FROM_BROWSER=${YTDLP_COOKIES_FROM_BROWSER}
YTDLP_COOKIES_FROM_BROWSER_PROFILE=${YTDLP_COOKIES_FROM_BROWSER_PROFILE}
YTDLP_JS_RUNTIMES=${YTDLP_JS_RUNTIMES}
YTDLP_REMOTE_COMPONENTS=${YTDLP_REMOTE_COMPONENTS}
PROXY_ENABLED=${PROXY_ENABLED}
PROXY_CAPTIONS_ONLY=${PROXY_CAPTIONS_ONLY}
PROXY_ROTATION_MODE=${PROXY_ROTATION_MODE}
PROXY_MAX_RETRIES=${PROXY_MAX_RETRIES}
PROXY_BACKOFF_SECONDS=${PROXY_BACKOFF_SECONDS}
NO_PROXY=${NO_PROXY}
PROXY_AUTOGENERATE=${PROXY_AUTOGENERATE}
PROXY_AUTOGENERATE_TEMPLATE=${PROXY_AUTOGENERATE_TEMPLATE}
PROXY_AUTOGENERATE_START=${PROXY_AUTOGENERATE_START}
PROXY_AUTOGENERATE_END=${PROXY_AUTOGENERATE_END}
EOF

if [[ -n "${YTDLP_COOKIES:-}" ]]; then
  echo "YTDLP_COOKIES=${YTDLP_COOKIES}" >>"${ENV_FILE}"
fi
if [[ -n "${YTDLP_COOKIES_TEXT:-}" ]]; then
  echo "YTDLP_COOKIES_TEXT=${YTDLP_COOKIES_TEXT}" >>"${ENV_FILE}"
fi
if [[ -n "${YTDLP_COOKIES_B64:-}" ]]; then
  echo "YTDLP_COOKIES_B64=${YTDLP_COOKIES_B64}" >>"${ENV_FILE}"
fi
if [[ "${PROXY_ENABLED,,}" == "true" ]]; then
  if [[ -n "${HTTP_PROXY:-}" ]]; then
    echo "HTTP_PROXY=${HTTP_PROXY}" >>"${ENV_FILE}"
  fi
  if [[ -n "${HTTPS_PROXY:-}" ]]; then
    echo "HTTPS_PROXY=${HTTPS_PROXY}" >>"${ENV_FILE}"
  fi
  if [[ -n "${ALL_PROXY:-}" ]]; then
    echo "ALL_PROXY=${ALL_PROXY}" >>"${ENV_FILE}"
  fi
  if [[ -n "${PROXY_POOL:-}" ]]; then
    echo "PROXY_POOL=${PROXY_POOL}" >>"${ENV_FILE}"
  fi
fi

gcloud compute scp "${SRC_TAR}" "${WORKER_VM_NAME}:${REMOTE_TAR}" --zone "${WORKER_VM_ZONE}" >/dev/null
gcloud compute scp "${ENV_FILE}" "${WORKER_VM_NAME}:${REMOTE_ENV}" --zone "${WORKER_VM_ZONE}" >/dev/null

gcloud compute ssh "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --command "
set -euo pipefail
sudo mkdir -p /opt/capstone /etc/capstone /cloudsql
sudo tar -xzf ${REMOTE_TAR} -C /opt/capstone
sudo chown -R ${WORKER_VM_USER}:${WORKER_VM_USER} /opt/capstone /cloudsql
# Ensure supported JS runtime for yt-dlp challenge solving.
if ! command -v node >/dev/null 2>&1 || ! node --version | grep -Eq '^v2[2-9]\\.'; then
  sudo bash -lc 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash -'
  sudo apt-get install -y nodejs >/dev/null
fi
sudo -u ${WORKER_VM_USER} python3 -m venv /opt/capstone/.venv
sudo -u ${WORKER_VM_USER} /opt/capstone/.venv/bin/pip install --upgrade pip >/dev/null
sudo -u ${WORKER_VM_USER} /opt/capstone/.venv/bin/pip install -r /opt/capstone/requirements.txt >/dev/null
sudo mv ${REMOTE_ENV} /etc/capstone/worker.env
sudo chown root:root /etc/capstone/worker.env
sudo chmod 600 /etc/capstone/worker.env
sudo systemctl daemon-reload
sudo systemctl enable cloud-sql-proxy capstone-worker >/dev/null
sudo systemctl restart cloud-sql-proxy
sudo systemctl restart capstone-worker
sudo systemctl --no-pager --full status capstone-worker | head -n 30
" >/dev/null

echo "VM worker deployed on ${WORKER_VM_NAME} (${WORKER_VM_ZONE})."

if [[ "${DISABLE_CLOUD_RUN_WORKER,,}" == "true" ]]; then
  if gcloud run services describe "${WORKER_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" >/dev/null 2>&1; then
    gcloud run services update "${WORKER_SERVICE}" \
      --project "${PROJECT_ID}" \
      --region "${REGION}" \
      --min-instances 0 \
      --update-env-vars "RQ_QUEUE_NAME=${CLOUD_RUN_SHADOW_QUEUE}" \
      >/dev/null
    echo "Scaled Cloud Run worker '${WORKER_SERVICE}' to min-instances=0 and isolated it to queue '${CLOUD_RUN_SHADOW_QUEUE}' (VM worker is primary)."
  fi
fi

echo "To verify logs:"
echo "  gcloud compute ssh ${WORKER_VM_NAME} --zone ${WORKER_VM_ZONE} --command 'sudo journalctl -u capstone-worker -f'"
