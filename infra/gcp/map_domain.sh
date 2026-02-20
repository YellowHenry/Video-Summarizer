#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/load_python_env.sh"

: "${REGION:?REGION is required}"
: "${SERVICE:?SERVICE is required}"
: "${DOMAIN:?DOMAIN is required}"

gcloud beta run domain-mappings create \
  --service "${SERVICE}" \
  --domain "${DOMAIN}" \
  --region "${REGION}" \
  || true

echo "Domain mapping requested for ${SERVICE} -> ${DOMAIN}."
echo "Check DNS records with:"
echo "  gcloud beta run domain-mappings describe --domain ${DOMAIN} --region ${REGION}"
