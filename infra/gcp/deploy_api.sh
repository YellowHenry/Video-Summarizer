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

resolve_openai_api_key

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
: "${OPENAI_API_KEY:?OPENAI_API_KEY is required (or set backend/config.py OPENAI_API_KEY)}"
: "${GOOGLE_OAUTH_CLIENT_ID:?GOOGLE_OAUTH_CLIENT_ID is required}"
: "${API_SERVICE:=audio-summarizer-api}"
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
OPENAI_API_KEY: "${OPENAI_API_KEY}"
RQ_QUEUE_NAME: "jobs"
RQ_RETRY_MAX: "${RQ_RETRY_MAX}"
RQ_RETRY_INTERVALS: "${RQ_RETRY_INTERVALS}"
CORS_ALLOW_ORIGINS: "${CORS_ALLOW_ORIGINS}"
WEBAPP_ENABLE_PGVECTOR: "${WEBAPP_ENABLE_PGVECTOR}"
WEBAPP_GOOGLE_CLIENT_ID: "${GOOGLE_OAUTH_CLIENT_ID}"
EOF

gcloud run deploy "${API_SERVICE}" \
  --image "${API_URI}" \
  --region "${REGION}" \
  --allow-unauthenticated \
  --service-account "${API_SA_EMAIL}" \
  --vpc-connector "${VPC_CONNECTOR}" \
  --vpc-egress private-ranges-only \
  --add-cloudsql-instances "${SQL_CONN}" \
  --env-vars-file "${ENV_FILE}"

API_URL="$(gcloud run services describe "${API_SERVICE}" --region "${REGION}" --format='value(status.url)')"
gcloud run services update "${API_SERVICE}" \
  --region "${REGION}" \
  --update-env-vars "API_BASE_URL=${API_URL}"

echo "API deployed: ${API_URL}"
