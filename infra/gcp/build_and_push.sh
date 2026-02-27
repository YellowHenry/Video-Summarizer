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
: "${IMAGE_API:=}"
: "${IMAGE_WEB:=}"
: "${VITE_API_BASE_URL:=http://localhost:8000}"
: "${BUILD_TARGETS:=api,web}"

API_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE_API}:latest"
REGISTRY_HOST="${REGION}-docker.pkg.dev"
TARGETS="${BUILD_TARGETS,,}"
TARGETS="${TARGETS// /}"
PUSHED_IMAGES=()

target_enabled() {
  local target="$1"
  if [[ "${TARGETS}" == "all" ]]; then
    return 0
  fi
  case ",${TARGETS}," in
    *",${target},"*) return 0 ;;
    *) return 1 ;;
  esac
}

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

if target_enabled "api"; then
  : "${IMAGE_API:?IMAGE_API is required when BUILD_TARGETS includes api}"
  build_image . -f Dockerfile.api -t "${API_URI}"
  docker push "${API_URI}"
  PUSHED_IMAGES+=("${API_URI}")
fi

if target_enabled "web" && [[ -n "${IMAGE_WEB}" ]]; then
  WEB_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE_WEB}:latest"
  build_image ./web -f web/Dockerfile --build-arg VITE_API_BASE_URL="${VITE_API_BASE_URL}" -t "${WEB_URI}"
  docker push "${WEB_URI}"
  PUSHED_IMAGES+=("${WEB_URI}")
fi

if [[ "${#PUSHED_IMAGES[@]}" -eq 0 ]]; then
  echo "No images were built. Set BUILD_TARGETS to include api and/or web." >&2
  exit 1
fi

echo "Pushed:"
for image in "${PUSHED_IMAGES[@]}"; do
  echo "  ${image}"
done
