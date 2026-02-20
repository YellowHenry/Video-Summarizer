#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/load_python_env.sh"

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${REPO:?REPO is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${SQL_EDITION:=ENTERPRISE}"
: "${SQL_TIER:=db-custom-2-7680}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${REDIS_INSTANCE:?REDIS_INSTANCE is required}"
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
  if ! gcloud sql instances create "${CLOUD_SQL_INSTANCE}" \
    --database-version=POSTGRES_16 \
    --edition="${SQL_EDITION}" \
    --tier="${SQL_TIER}" \
    --region="${REGION}"; then
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

gcloud redis instances create "${REDIS_INSTANCE}" \
  --size=1 \
  --region="${REGION}" \
  --redis-version=redis_7_2 \
  --tier=basic \
  || true

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
