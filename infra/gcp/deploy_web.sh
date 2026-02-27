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
: "${IMAGE_WEB:?IMAGE_WEB is required}"
: "${GOOGLE_OAUTH_CLIENT_ID:?GOOGLE_OAUTH_CLIENT_ID is required}"
: "${API_SERVICE:=audio-summarizer-api}"
: "${WEB_SERVICE:=audio-summarizer-web}"
: "${WEB_PORT:=80}"

WEB_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE_WEB}:latest"
API_URL="$(gcloud run services describe "${API_SERVICE}" --region "${REGION}" --format='value(status.url)')"
REGISTRY_HOST="${REGION}-docker.pkg.dev"

ensure_registry_auth() {
  gcloud auth configure-docker "${REGISTRY_HOST}" --quiet || true
  if command -v docker-credential-gcloud >/dev/null 2>&1; then
    return
  fi

  echo "docker-credential-gcloud not found; falling back to access-token docker login for ${REGISTRY_HOST}."
  if gcp_has_python; then
    gcp_run_python - "${REGISTRY_HOST}" <<'PY'
import json
from pathlib import Path
import sys

registry = sys.argv[1]
cfg = Path.home() / ".docker" / "config.json"
if cfg.exists():
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        data = {}
else:
    data = {}

cred_helpers = data.get("credHelpers")
if isinstance(cred_helpers, dict):
    cred_helpers.pop(registry, None)
    if not cred_helpers:
        data.pop("credHelpers", None)

cfg.parent.mkdir(parents=True, exist_ok=True)
cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY
  fi

  gcloud auth print-access-token | docker login -u oauth2accesstoken --password-stdin "https://${REGISTRY_HOST}"
}

build_image() {
  local context="$1"
  shift
  if docker build "$@" "${context}"; then
    return 0
  fi
  echo "Docker BuildKit build failed; retrying with DOCKER_BUILDKIT=0 (legacy builder)."
  DOCKER_BUILDKIT=0 docker build "$@" "${context}"
}

ensure_registry_auth

build_image ./web -f web/Dockerfile --build-arg VITE_API_BASE_URL="${API_URL}" --build-arg VITE_GOOGLE_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID}" -t "${WEB_URI}"
docker push "${WEB_URI}"

gcloud run deploy "${WEB_SERVICE}" \
  --image "${WEB_URI}" \
  --region "${REGION}" \
  --port "${WEB_PORT}" \
  --allow-unauthenticated

WEB_URL="$(gcloud run services describe "${WEB_SERVICE}" --region "${REGION}" --format='value(status.url)')"
echo "Web deployed: ${WEB_URL}"
