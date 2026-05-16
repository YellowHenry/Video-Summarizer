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

validate_openai_api_key() {
  local code=""
  local out_file
  out_file="$(mktemp /tmp/capstone-openai-key-check.XXXXXX)"
  code="$(
    curl -sS -o "${out_file}" -w "%{http_code}" \
      -H "Authorization: Bearer ${OPENAI_API_KEY}" \
      https://api.openai.com/v1/models || true
  )"
  rm -f "${out_file}" >/dev/null 2>&1 || true
  if [[ "${code}" != "200" ]]; then
    echo "OpenAI API key validation failed before worker deploy (HTTP ${code})." >&2
    echo "Update Secret Manager secret '${OPENAI_SECRET_NAME:-openai-api-key}' or clear stale local OPENAI_API_KEY, then retry." >&2
    exit 1
  fi
}

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${REDIS_RUNTIME:=memorystore}"
: "${BUCKET_NAME:?BUCKET_NAME is required}"
: "${OPENAI_SECRET_NAME:=}"
if [[ -z "${OPENAI_SECRET_NAME}" ]]; then
  resolve_openai_api_key
else
  # When a Secret Manager binding is configured, it is the production source of
  # truth. Always read it here so stale local shell variables cannot overwrite
  # the worker VM environment during deploy.
  OPENAI_API_KEY="$(gcloud secrets versions access latest --secret="${OPENAI_SECRET_NAME}")"
  export OPENAI_API_KEY
fi
: "${OPENAI_API_KEY:?OPENAI_API_KEY or OPENAI_SECRET_NAME is required}"
validate_openai_api_key
: "${OPENAI_TRUST_ENV_PROXY:=false}"
: "${SUMMARIZER_MAX_TOKENS:=800}"
: "${WORKER_VM_NAME:=audio-summarizer-worker-vm}"
: "${WORKER_VM_ZONE:=us-central1-a}"
: "${WORKER_VM_USER:=worker}"
: "${WORKER_VM_NETWORK:=default}"
: "${VPC_CONNECTOR_RANGE:=10.8.0.0/28}"
: "${REDIS_VM_PORT:=6379}"
: "${REDIS_VM_REQUIREPASS:=}"
: "${REDIS_VM_FIREWALL_RULE:=capstone-worker-redis-allow}"
: "${REDIS_VM_ALLOWED_SOURCE:=}"
: "${WORKER_SERVICE:=audio-summarizer-worker}"
: "${DISABLE_CLOUD_RUN_WORKER:=true}"
: "${CLOUD_RUN_SHADOW_QUEUE:=cloudrun-disabled}"
: "${WEB_SERVICE:=audio-summarizer-web}"
: "${WEBAPP_ENABLE_PGVECTOR:=true}"
: "${RQ_RETRY_MAX:=3}"
: "${RQ_RETRY_INTERVALS:=30,120,300}"
: "${WEB_APP_BASE_URL:=}"
: "${DIGEST_SWEEP_INTERVAL_MINUTES:=15}"
: "${DIGEST_PROFILE_MAX_JOBS:=20}"
: "${DIGEST_MAX_ITEMS_PER_EMAIL:=10}"
: "${DIGEST_JOB_EXCERPT_CHARS:=240}"
: "${DIGEST_SEND_HOUR_LOCAL:=8}"
: "${DIGEST_WEEKLY_WEEKDAY:=0}"
: "${SMTP_HOST:=}"
: "${SMTP_PORT:=587}"
: "${SMTP_USER:=}"
: "${SMTP_PASSWORD:=}"
: "${SMTP_FROM:=}"
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
: "${PROXY_POOL:=}"
: "${PROXY_AUTOGENERATE:=false}"
: "${PROXY_AUTOGENERATE_TEMPLATE:=}"
: "${PROXY_AUTOGENERATE_START:=1}"
: "${PROXY_AUTOGENERATE_END:=1}"

if [[ "${PROXY_ENABLED,,}" == "true" ]]; then
  if [[ -z "${PROXY_POOL:-}" && ! ( "${PROXY_AUTOGENERATE,,}" == "true" && -n "${PROXY_AUTOGENERATE_TEMPLATE:-}" ) ]]; then
    echo "PROXY_ENABLED=true but no proxy endpoints were configured." >&2
    echo "Set PROXY_POOL, or set PROXY_AUTOGENERATE=true with PROXY_AUTOGENERATE_TEMPLATE." >&2
    exit 1
  fi
fi

gcloud config set project "${PROJECT_ID}" >/dev/null

if ! gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" >/dev/null 2>&1; then
  echo "Worker VM '${WORKER_VM_NAME}' not found in zone '${WORKER_VM_ZONE}'." >&2
  echo "Run: bash infra/gcp/provision_worker_vm.sh" >&2
  exit 1
fi

INSTANCE_CONN="${PROJECT_ID}:${REGION}:${CLOUD_SQL_INSTANCE}"
WORKER_VM_INTERNAL_IP="$(gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --format='value(networkInterfaces[0].networkIP)')"
REDIS_URL="redis://localhost:6379/0"
REDIS_CONF_FILE=""
REMOTE_REDIS_CONF="/tmp/capstone-redis-$(date +%s)-$$.conf"

if [[ "${REDIS_RUNTIME}" == "worker_vm" ]]; then
  : "${REDIS_VM_REQUIREPASS:?REDIS_VM_REQUIREPASS is required when REDIS_RUNTIME=worker_vm}"
  if [[ -z "${REDIS_VM_ALLOWED_SOURCE}" ]]; then
    REDIS_VM_ALLOWED_SOURCE="${VPC_CONNECTOR_RANGE}"
  fi
  if gcloud compute firewall-rules describe "${REDIS_VM_FIREWALL_RULE}" >/dev/null 2>&1; then
    gcloud compute firewall-rules update "${REDIS_VM_FIREWALL_RULE}" \
      --source-ranges="${REDIS_VM_ALLOWED_SOURCE}" \
      --target-tags="capstone-worker-vm" \
      >/dev/null
  else
    gcloud compute firewall-rules create "${REDIS_VM_FIREWALL_RULE}" \
      --direction=INGRESS \
      --network="${WORKER_VM_NETWORK}" \
      --action=ALLOW \
      --rules="tcp:${REDIS_VM_PORT}" \
      --source-ranges="${REDIS_VM_ALLOWED_SOURCE}" \
      --target-tags="capstone-worker-vm" \
      >/dev/null
  fi
  REDIS_URL="redis://:${REDIS_VM_REQUIREPASS}@127.0.0.1:${REDIS_VM_PORT}/0"
  REDIS_CONF_FILE="$(mktemp /tmp/capstone-worker-redis.XXXXXX.conf)"
  cat >"${REDIS_CONF_FILE}" <<EOF
# Managed by infra/gcp/deploy_worker_vm.sh
bind 127.0.0.1 ${WORKER_VM_INTERNAL_IP}
protected-mode yes
port ${REDIS_VM_PORT}
requirepass ${REDIS_VM_REQUIREPASS}
appendonly yes
maxmemory-policy noeviction
EOF
else
  : "${REDIS_INSTANCE:?REDIS_INSTANCE is required when REDIS_RUNTIME=${REDIS_RUNTIME}}"
  REDIS_HOST="$(gcloud redis instances describe "${REDIS_INSTANCE}" --region "${REGION}" --format='value(host)')"
  REDIS_URL="redis://${REDIS_HOST}:6379/0"
fi

if [[ -z "${WEB_APP_BASE_URL}" ]]; then
  WEB_APP_BASE_URL="$(gcloud run services describe "${WEB_SERVICE}" --region "${REGION}" --format='value(status.url)' 2>/dev/null || true)"
fi

SRC_TAR="$(mktemp /tmp/capstone-worker-src.XXXXXX.tgz)"
ENV_FILE="$(mktemp /tmp/capstone-worker-env.XXXXXX)"
REMOTE_TAR="/tmp/capstone-worker-src-$(date +%s)-$$.tgz"
REMOTE_ENV="/tmp/capstone-worker-$(date +%s)-$$.env"

cleanup() {
  rm -f "${SRC_TAR}" "${ENV_FILE}" >/dev/null 2>&1 || true
  if [[ -n "${REDIS_CONF_FILE}" ]]; then
    rm -f "${REDIS_CONF_FILE}" >/dev/null 2>&1 || true
  fi
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
REDIS_URL=${REDIS_URL}
OBJECT_STORAGE_BACKEND=gcs
GCS_BUCKET=${BUCKET_NAME}
GOOGLE_CLOUD_PROJECT=${PROJECT_ID}
# Leave quota-project unset: the worker VM service account doesn't need
# user-project billing overrides for GCS, and setting one forces
# serviceusage.services.use checks that can break artifact writes.
GCE_METADATA_MTLS_MODE=none
OPENAI_API_KEY=${OPENAI_API_KEY}
OPENAI_TRUST_ENV_PROXY=${OPENAI_TRUST_ENV_PROXY}
SUMMARIZER_MAX_TOKENS=${SUMMARIZER_MAX_TOKENS}
RQ_QUEUE_NAME=jobs
RQ_RETRY_MAX=${RQ_RETRY_MAX}
RQ_RETRY_INTERVALS=${RQ_RETRY_INTERVALS}
WEBAPP_ENABLE_PGVECTOR=${WEBAPP_ENABLE_PGVECTOR}
WEB_APP_BASE_URL=${WEB_APP_BASE_URL}
DIGEST_SWEEP_INTERVAL_MINUTES=${DIGEST_SWEEP_INTERVAL_MINUTES}
DIGEST_PROFILE_MAX_JOBS=${DIGEST_PROFILE_MAX_JOBS}
DIGEST_MAX_ITEMS_PER_EMAIL=${DIGEST_MAX_ITEMS_PER_EMAIL}
DIGEST_JOB_EXCERPT_CHARS=${DIGEST_JOB_EXCERPT_CHARS}
DIGEST_SEND_HOUR_LOCAL=${DIGEST_SEND_HOUR_LOCAL}
DIGEST_WEEKLY_WEEKDAY=${DIGEST_WEEKLY_WEEKDAY}
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
PROXY_AUTOGENERATE=${PROXY_AUTOGENERATE}
PROXY_AUTOGENERATE_TEMPLATE=${PROXY_AUTOGENERATE_TEMPLATE}
PROXY_AUTOGENERATE_START=${PROXY_AUTOGENERATE_START}
PROXY_AUTOGENERATE_END=${PROXY_AUTOGENERATE_END}
EOF

write_env_var() {
  local name="$1"
  local value="$2"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\\\$}"
  value="${value//\`/\\\`}"
  printf '%s="%s"\n' "${name}" "${value}" >>"${ENV_FILE}"
}

if [[ -n "${SMTP_HOST:-}" ]]; then
  write_env_var "SMTP_HOST" "${SMTP_HOST}"
fi
if [[ -n "${SMTP_PORT:-}" ]]; then
  write_env_var "SMTP_PORT" "${SMTP_PORT}"
fi
if [[ -n "${SMTP_USER:-}" ]]; then
  write_env_var "SMTP_USER" "${SMTP_USER}"
fi
if [[ -n "${SMTP_PASSWORD:-}" ]]; then
  write_env_var "SMTP_PASSWORD" "${SMTP_PASSWORD}"
fi
if [[ -n "${SMTP_FROM:-}" ]]; then
  write_env_var "SMTP_FROM" "${SMTP_FROM}"
fi

if [[ -n "${YTDLP_COOKIES:-}" ]]; then
  write_env_var "YTDLP_COOKIES" "${YTDLP_COOKIES}"
fi
if [[ -n "${YTDLP_COOKIES_TEXT:-}" ]]; then
  write_env_var "YTDLP_COOKIES_TEXT" "${YTDLP_COOKIES_TEXT}"
fi
if [[ -n "${YTDLP_COOKIES_B64:-}" ]]; then
  write_env_var "YTDLP_COOKIES_B64" "${YTDLP_COOKIES_B64}"
fi
if [[ "${PROXY_ENABLED,,}" == "true" ]]; then
  # Intentionally do NOT export global HTTP(S)_PROXY / ALL_PROXY into the worker runtime.
  # The app routes caption traffic through proxies explicitly via backend/proxy_egress.py.
  # Keeping global proxy env vars out of worker.env prevents non-caption traffic
  # (for example GCS uploads, metadata calls) from being sent through residential proxies.
  if [[ -n "${PROXY_POOL:-}" ]]; then
    write_env_var "PROXY_POOL" "${PROXY_POOL}"
  fi
fi

gcloud compute scp "${SRC_TAR}" "${WORKER_VM_NAME}:${REMOTE_TAR}" --zone "${WORKER_VM_ZONE}" >/dev/null
gcloud compute scp "${ENV_FILE}" "${WORKER_VM_NAME}:${REMOTE_ENV}" --zone "${WORKER_VM_ZONE}" >/dev/null
if [[ "${REDIS_RUNTIME}" == "worker_vm" ]]; then
  gcloud compute scp "${REDIS_CONF_FILE}" "${WORKER_VM_NAME}:${REMOTE_REDIS_CONF}" --zone "${WORKER_VM_ZONE}" >/dev/null
fi

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
if [[ \"${REDIS_RUNTIME}\" == \"worker_vm\" ]]; then
  if ! command -v redis-server >/dev/null 2>&1; then
    sudo apt-get update >/dev/null
    sudo apt-get install -y redis-server >/dev/null
  fi
  if ! sudo grep -Fq 'include /etc/redis/redis-capstone.conf' /etc/redis/redis.conf; then
    echo 'include /etc/redis/redis-capstone.conf' | sudo tee -a /etc/redis/redis.conf >/dev/null
  fi
  sudo mv ${REMOTE_REDIS_CONF} /etc/redis/redis-capstone.conf
  sudo chown root:redis /etc/redis/redis-capstone.conf || sudo chown root:root /etc/redis/redis-capstone.conf
  sudo chmod 640 /etc/redis/redis-capstone.conf
  sudo systemctl enable redis-server >/dev/null
  sudo systemctl restart redis-server
fi
sudo mv ${REMOTE_ENV} /etc/capstone/worker.env
sudo chown root:root /etc/capstone/worker.env
sudo chmod 600 /etc/capstone/worker.env
sudo systemctl daemon-reload
sudo systemctl enable cloud-sql-proxy capstone-worker >/dev/null
sudo mkdir -p /cloudsql/${INSTANCE_CONN}
sudo rm -f /cloudsql/${INSTANCE_CONN}/.s.PGSQL.5432 /cloudsql/${INSTANCE_CONN}/.s.PGSQL.5432.lock >/dev/null 2>&1 || true
sudo systemctl restart cloud-sql-proxy
sudo systemctl restart capstone-worker
sudo systemctl --no-pager --full status capstone-worker | head -n 30
" >/dev/null

echo "VM worker deployed on ${WORKER_VM_NAME} (${WORKER_VM_ZONE})."
if [[ "${REDIS_RUNTIME}" == "worker_vm" ]]; then
  echo "VM Redis configured on ${WORKER_VM_NAME}:${REDIS_VM_PORT} (firewall source ${REDIS_VM_ALLOWED_SOURCE})."
fi

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
