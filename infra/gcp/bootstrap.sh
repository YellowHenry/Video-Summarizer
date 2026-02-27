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
: "${REPO:?REPO is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${SQL_EDITION:=ENTERPRISE}"
: "${SQL_TIER:=db-custom-2-7680}"
: "${SQL_STORAGE_TYPE:=}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${REDIS_RUNTIME:=memorystore}"
: "${BUCKET_NAME:?BUCKET_NAME is required}"
: "${NETWORK:=default}"
: "${VPC_CONNECTOR:=capstone-connector}"
: "${VPC_CONNECTOR_RANGE:=10.8.0.0/28}"
: "${API_SERVICE_ACCOUNT:=audio-summarizer-api-sa}"
: "${WORKER_SERVICE_ACCOUNT:=audio-summarizer-worker-sa}"

gcloud config set project "${PROJECT_ID}"

gcloud services enable \
  run.googleapis.com \
  vpcaccess.googleapis.com \
  compute.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com

gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="Capstone containers" \
  || true

if gcloud sql instances describe "${CLOUD_SQL_INSTANCE}" >/dev/null 2>&1; then
  echo "Cloud SQL instance '${CLOUD_SQL_INSTANCE}' already exists; skipping create."
else
  SQL_CREATE_ARGS=(
    sql instances create "${CLOUD_SQL_INSTANCE}"
    --database-version=POSTGRES_16
    --edition="${SQL_EDITION}"
    --tier="${SQL_TIER}"
    --region="${REGION}"
  )
  if [[ -n "${SQL_STORAGE_TYPE}" ]]; then
    SQL_CREATE_ARGS+=(--storage-type="${SQL_STORAGE_TYPE}")
  fi

  if ! gcloud "${SQL_CREATE_ARGS[@]}"; then
    cat >&2 <<EOF
Cloud SQL instance creation failed.
Tried:
  edition=${SQL_EDITION}
  tier=${SQL_TIER}

If you see an "Invalid Tier ... for (ENTERPRISE_PLUS) Edition" error, set:
  SQL_EDITION="ENTERPRISE_PLUS"
  SQL_TIER="db-perf-optimized-N-2"
in infra/gcp/deploy_config.py, then rerun.
EOF
    exit 1
  fi
fi

gcloud sql databases create "${DB_NAME}" --instance="${CLOUD_SQL_INSTANCE}" || true
gcloud sql users set-password "${DB_USER}" --instance="${CLOUD_SQL_INSTANCE}" --password="${DB_PASSWORD}" || \
  gcloud sql users create "${DB_USER}" --instance="${CLOUD_SQL_INSTANCE}" --password="${DB_PASSWORD}"

if [[ "${REDIS_RUNTIME}" == "worker_vm" ]]; then
  echo "REDIS_RUNTIME=worker_vm; skipping Memorystore provisioning."
else
  : "${REDIS_INSTANCE:?REDIS_INSTANCE is required when REDIS_RUNTIME=${REDIS_RUNTIME}}"
  gcloud redis instances create "${REDIS_INSTANCE}" \
    --size=1 \
    --region="${REGION}" \
    --redis-version=redis_7_2 \
    --tier=basic \
    || true
fi

gcloud storage buckets create "gs://${BUCKET_NAME}" --location="${REGION}" || true

gcloud compute networks vpc-access connectors create "${VPC_CONNECTOR}" \
  --region="${REGION}" \
  --network="${NETWORK}" \
  --range="${VPC_CONNECTOR_RANGE}" \
  || true

gcloud iam service-accounts create "${API_SERVICE_ACCOUNT}" \
  --display-name="Audio Summarizer API runtime" \
  || true

gcloud iam service-accounts create "${WORKER_SERVICE_ACCOUNT}" \
  --display-name="Audio Summarizer Worker runtime" \
  || true

API_SA_EMAIL="${API_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA_EMAIL="${WORKER_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"

for SA_EMAIL in "${API_SA_EMAIL}" "${WORKER_SA_EMAIL}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/cloudsql.client" \
    || true
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/logging.logWriter" \
    || true
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/monitoring.metricWriter" \
    || true
done

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${API_SA_EMAIL}" \
  --role="roles/storage.objectAdmin" \
  || true

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${WORKER_SA_EMAIL}" \
  --role="roles/storage.objectAdmin" \
  || true

echo "Bootstrap complete."
