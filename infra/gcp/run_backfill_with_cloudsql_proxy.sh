#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/load_python_env.sh"

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${BUCKET_NAME:?BUCKET_NAME is required}"
: "${LOCAL_STORAGE_ROOT:=storage}"
: "${CLOUD_SQL_PROXY_PORT:=5432}"
: "${BACKFILL_ENABLE_EMBEDDINGS:=true}"

if ! gcp_has_python; then
  echo "python/python3/py is required in PATH" >&2
  exit 1
fi

if ! command -v cloud-sql-proxy >/dev/null 2>&1; then
  echo "cloud-sql-proxy is required in PATH" >&2
  exit 1
fi

INSTANCE_CONN="${PROJECT_ID}:${REGION}:${CLOUD_SQL_INSTANCE}"
echo "Starting Cloud SQL proxy for ${INSTANCE_CONN} on 127.0.0.1:${CLOUD_SQL_PROXY_PORT}"

cloud-sql-proxy "${INSTANCE_CONN}" --address 127.0.0.1 --port "${CLOUD_SQL_PROXY_PORT}" >/tmp/cloud_sql_proxy.log 2>&1 &
PROXY_PID=$!

cleanup() {
  if kill -0 "${PROXY_PID}" >/dev/null 2>&1; then
    kill "${PROXY_PID}" >/dev/null 2>&1 || true
    wait "${PROXY_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Wait for proxy to accept connections
ready=0
for _ in $(seq 1 30); do
  if gcp_run_python - "${CLOUD_SQL_PROXY_PORT}" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(1.0)
try:
    sock.connect(("127.0.0.1", port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
  then
    ready=1
    break
  fi
  sleep 1
done

if [[ "${ready}" != "1" ]]; then
  echo "Cloud SQL proxy failed to start. Logs:" >&2
  cat /tmp/cloud_sql_proxy.log >&2 || true
  exit 1
fi

export DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${CLOUD_SQL_PROXY_PORT}/${DB_NAME}"
export OBJECT_STORAGE_BACKEND="gcs"
export GCS_BUCKET="${BUCKET_NAME}"

if [[ "${BACKFILL_ENABLE_EMBEDDINGS}" != "true" ]]; then
  echo "BACKFILL_ENABLE_EMBEDDINGS=${BACKFILL_ENABLE_EMBEDDINGS}; running without embeddings (OPENAI_API_KEY cleared)."
  unset OPENAI_API_KEY
elif [[ -z "${OPENAI_API_KEY:-}" ]] && gcp_has_python; then
  key="$(gcp_run_python "${SCRIPT_DIR}/read_openai_key.py" 2>/dev/null || true)"
  if [[ -n "${key}" ]]; then
    export OPENAI_API_KEY="${key}"
  fi
fi

echo "Running webapp DB migrations..."
gcp_run_python -m backend.webapp.migrate

echo "Running storage backfill from ${LOCAL_STORAGE_ROOT}..."
gcp_run_python -m backend.webapp.backfill_from_storage --storage-root "${LOCAL_STORAGE_ROOT}"

echo "Backfill complete."
