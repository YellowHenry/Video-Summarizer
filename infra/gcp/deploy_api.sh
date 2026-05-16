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
  local key="$1"
  local label="$2"
  local code=""
  local out_file
  out_file="$(mktemp /tmp/capstone-openai-key-check.XXXXXX)"
  code="$(
    curl -sS -o "${out_file}" -w "%{http_code}" \
      -H "Authorization: Bearer ${key}" \
      https://api.openai.com/v1/models || true
  )"
  rm -f "${out_file}" >/dev/null 2>&1 || true
  if [[ "${code}" != "200" ]]; then
    echo "OpenAI API key validation failed before API deploy (HTTP ${code}, source: ${label})." >&2
    echo "Update Secret Manager secret '${OPENAI_SECRET_NAME:-openai-api-key}' or clear stale local OPENAI_API_KEY, then retry." >&2
    exit 1
  fi
}

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${REPO:?REPO is required}"
: "${IMAGE_API:?IMAGE_API is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${REDIS_RUNTIME:=memorystore}"
: "${BUCKET_NAME:?BUCKET_NAME is required}"
: "${OPENAI_SECRET_NAME:=}"
if [[ -z "${OPENAI_SECRET_NAME}" ]]; then
  resolve_openai_api_key
  : "${OPENAI_API_KEY:?OPENAI_API_KEY or OPENAI_SECRET_NAME is required}"
  validate_openai_api_key "${OPENAI_API_KEY}" "OPENAI_API_KEY"
else
  OPENAI_KEY_FOR_VALIDATION="$(gcloud secrets versions access latest --secret="${OPENAI_SECRET_NAME}")"
  validate_openai_api_key "${OPENAI_KEY_FOR_VALIDATION}" "Secret Manager:${OPENAI_SECRET_NAME}"
  unset OPENAI_KEY_FOR_VALIDATION
fi
: "${GOOGLE_OAUTH_CLIENT_ID:?GOOGLE_OAUTH_CLIENT_ID is required}"
: "${API_SERVICE:=audio-summarizer-api}"
: "${WEB_SERVICE:=audio-summarizer-web}"
: "${API_SERVICE_ACCOUNT:=audio-summarizer-api-sa}"
: "${VPC_CONNECTOR:=capstone-connector}"
: "${WORKER_VM_NAME:=audio-summarizer-worker-vm}"
: "${WORKER_VM_ZONE:=us-central1-a}"
: "${REDIS_VM_PORT:=6379}"
: "${REDIS_VM_REQUIREPASS:=}"
: "${CORS_ALLOW_ORIGINS:=*}"
: "${WEBAPP_ENABLE_PGVECTOR:=true}"
: "${RQ_RETRY_MAX:=3}"
: "${RQ_RETRY_INTERVALS:=30,120,300}"
: "${WEB_APP_BASE_URL:=}"
: "${DIGEST_SWEEP_SECRET:=}"
: "${DIGEST_SWEEP_INTERVAL_MINUTES:=15}"
: "${DIGEST_PROFILE_MAX_JOBS:=20}"
: "${DIGEST_MAX_ITEMS_PER_EMAIL:=10}"
: "${DIGEST_JOB_EXCERPT_CHARS:=240}"
: "${DIGEST_SEND_HOUR_LOCAL:=8}"
: "${DIGEST_WEEKLY_WEEKDAY:=0}"
: "${DIGEST_SWEEP_JOB_NAME:=audio-summarizer-digest-sweep}"
: "${SMTP_HOST:=}"
: "${SMTP_PORT:=587}"
: "${SMTP_USER:=}"
: "${SMTP_PASSWORD:=}"
: "${SMTP_FROM:=}"
: "${BUILD_AND_PUSH_API:=true}"

gcloud config set project "${PROJECT_ID}" >/dev/null

API_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE_API}:latest"
SQL_CONN="${PROJECT_ID}:${REGION}:${CLOUD_SQL_INSTANCE}"
API_SA_EMAIL="${API_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"

if [[ "${REDIS_RUNTIME}" == "worker_vm" ]]; then
  : "${REDIS_VM_REQUIREPASS:?REDIS_VM_REQUIREPASS is required when REDIS_RUNTIME=worker_vm}"
  REDIS_HOST="$(gcloud compute instances describe "${WORKER_VM_NAME}" --zone "${WORKER_VM_ZONE}" --format='value(networkInterfaces[0].networkIP)')"
  REDIS_URL="redis://:${REDIS_VM_REQUIREPASS}@${REDIS_HOST}:${REDIS_VM_PORT}/0"
else
  : "${REDIS_INSTANCE:?REDIS_INSTANCE is required when REDIS_RUNTIME=${REDIS_RUNTIME}}"
  REDIS_HOST="$(gcloud redis instances describe "${REDIS_INSTANCE}" --region "${REGION}" --format='value(host)')"
  REDIS_URL="redis://${REDIS_HOST}:6379/0"
fi

if [[ -z "${WEB_APP_BASE_URL}" ]]; then
  WEB_APP_BASE_URL="$(gcloud run services describe "${WEB_SERVICE}" --region "${REGION}" --format='value(status.url)' 2>/dev/null || true)"
fi

if [[ "${BUILD_AND_PUSH_API,,}" == "true" ]]; then
  echo "Building and pushing API image..."
  BUILD_TARGETS="api" bash "${SCRIPT_DIR}/build_and_push.sh"
fi

ENV_FILE="$(mktemp)"
cleanup() {
  rm -f "${ENV_FILE}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

cat >"${ENV_FILE}" <<EOF
DATABASE_URL: "postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${SQL_CONN}"
REDIS_URL: "${REDIS_URL}"
OBJECT_STORAGE_BACKEND: "gcs"
GCS_BUCKET: "${BUCKET_NAME}"
GOOGLE_CLOUD_PROJECT: "${PROJECT_ID}"
# Force plain HTTP metadata on Google runtimes. The google-cloud-storage
# path can otherwise auto-enable metadata mTLS and fail certificate
# verification in this stack.
GCE_METADATA_MTLS_MODE: "none"
RQ_QUEUE_NAME: "jobs"
RQ_RETRY_MAX: "${RQ_RETRY_MAX}"
RQ_RETRY_INTERVALS: "${RQ_RETRY_INTERVALS}"
CORS_ALLOW_ORIGINS: "${CORS_ALLOW_ORIGINS}"
WEBAPP_ENABLE_PGVECTOR: "${WEBAPP_ENABLE_PGVECTOR}"
WEBAPP_GOOGLE_CLIENT_ID: "${GOOGLE_OAUTH_CLIENT_ID}"
WEB_APP_BASE_URL: "${WEB_APP_BASE_URL}"
DIGEST_SWEEP_SECRET: "${DIGEST_SWEEP_SECRET}"
DIGEST_SWEEP_INTERVAL_MINUTES: "${DIGEST_SWEEP_INTERVAL_MINUTES}"
DIGEST_PROFILE_MAX_JOBS: "${DIGEST_PROFILE_MAX_JOBS}"
DIGEST_MAX_ITEMS_PER_EMAIL: "${DIGEST_MAX_ITEMS_PER_EMAIL}"
DIGEST_JOB_EXCERPT_CHARS: "${DIGEST_JOB_EXCERPT_CHARS}"
DIGEST_SEND_HOUR_LOCAL: "${DIGEST_SEND_HOUR_LOCAL}"
DIGEST_WEEKLY_WEEKDAY: "${DIGEST_WEEKLY_WEEKDAY}"
SMTP_HOST: "${SMTP_HOST}"
SMTP_PORT: "${SMTP_PORT}"
SMTP_USER: "${SMTP_USER}"
SMTP_PASSWORD: "${SMTP_PASSWORD}"
SMTP_FROM: "${SMTP_FROM}"
EOF

if [[ -z "${OPENAI_SECRET_NAME}" ]]; then
  echo "OPENAI_API_KEY: \"${OPENAI_API_KEY}\"" >>"${ENV_FILE}"
fi

SECRET_ARGS=()
if [[ -n "${OPENAI_SECRET_NAME}" ]]; then
  SECRET_ARGS=(--set-secrets "OPENAI_API_KEY=${OPENAI_SECRET_NAME}:latest")
fi

gcloud run deploy "${API_SERVICE}" \
  --image "${API_URI}" \
  --region "${REGION}" \
  --allow-unauthenticated \
  --service-account "${API_SA_EMAIL}" \
  --vpc-connector "${VPC_CONNECTOR}" \
  --vpc-egress private-ranges-only \
  --add-cloudsql-instances "${SQL_CONN}" \
  --env-vars-file "${ENV_FILE}" \
  "${SECRET_ARGS[@]}"

API_URL="$(gcloud run services describe "${API_SERVICE}" --region "${REGION}" --format='value(status.url)')"
gcloud run services update "${API_SERVICE}" \
  --region "${REGION}" \
  --update-env-vars "API_BASE_URL=${API_URL}"

if [[ -n "${DIGEST_SWEEP_SECRET}" ]]; then
  if gcloud scheduler jobs describe "${DIGEST_SWEEP_JOB_NAME}" --location "${REGION}" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "${DIGEST_SWEEP_JOB_NAME}" \
      --location "${REGION}" \
      --schedule "*/${DIGEST_SWEEP_INTERVAL_MINUTES} * * * *" \
      --uri "${API_URL}/internal/digests/sweep" \
      --http-method POST \
      --update-headers "X-Capstone-Digest-Secret=${DIGEST_SWEEP_SECRET}" \
      --time-zone "UTC" \
      >/dev/null
  else
    gcloud scheduler jobs create http "${DIGEST_SWEEP_JOB_NAME}" \
      --location "${REGION}" \
      --schedule "*/${DIGEST_SWEEP_INTERVAL_MINUTES} * * * *" \
      --uri "${API_URL}/internal/digests/sweep" \
      --http-method POST \
      --headers "X-Capstone-Digest-Secret=${DIGEST_SWEEP_SECRET}" \
      --time-zone "UTC" \
      >/dev/null
  fi
  echo "Digest sweep scheduler configured: ${DIGEST_SWEEP_JOB_NAME}"
else
  echo "DIGEST_SWEEP_SECRET not set; skipping Cloud Scheduler digest job."
fi

echo "API deployed: ${API_URL}"
