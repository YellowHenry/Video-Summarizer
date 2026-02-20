#!/usr/bin/env bash

# Load deploy vars from infra/gcp/deploy_config.py.
# Existing shell env vars take precedence.

gcp_try_windows_gcloud_config() {
  if [[ -n "${CLOUDSDK_CONFIG:-}" ]]; then
    return
  fi

  # Only applies to WSL-style environments.
  case "$(uname -r 2>/dev/null)" in
    *[Mm]icrosoft*|*WSL*) ;;
    *) return ;;
  esac

  if ! command -v cmd.exe >/dev/null 2>&1; then
    return
  fi

  local win_user=""
  win_user="$(cmd.exe /c echo %USERNAME% 2>/dev/null | tr -d '\r\n' || true)"
  if [[ -z "${win_user}" ]]; then
    return
  fi

  local win_cfg="/mnt/c/Users/${win_user}/AppData/Roaming/gcloud"
  if [[ -d "${win_cfg}" ]]; then
    export CLOUDSDK_CONFIG="${win_cfg}"
  fi
}

gcp_has_python() {
  if command -v python >/dev/null 2>&1; then
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  if command -v py >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

gcp_run_python() {
  if command -v python >/dev/null 2>&1; then
    python "$@"
    return $?
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 "$@"
    return $?
  fi
  if command -v py >/dev/null 2>&1; then
    py -3 "$@"
    return $?
  fi
  return 127
}

gcp_try_windows_gcloud_config

if ! gcp_has_python; then
  return 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_py_exports="$(gcp_run_python "${SCRIPT_DIR}/load_python_env.py" 2>/dev/null || true)"
if [[ -n "${_py_exports}" ]]; then
  eval "${_py_exports}"
fi
unset _py_exports
