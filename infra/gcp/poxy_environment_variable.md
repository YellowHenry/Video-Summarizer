# I asked:
is the environment variable for whether or not proxie is allowed to be used anywhere besides downloading captions in a different place from the rest of environment variables in deploy_config,py, and if so why

# GPT said:

Short answer: **no**, that setting is not in some separate hidden config.

It's in the same place as the other env vars in `infra/gcp/deploy_config.py`:

- defaults in the `DeployConfig` dataclass:
  - `PROXY_ENABLED`, `PROXY_CAPTIONS_ONLY`, `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `PROXY_POOL`
- your active override block (`CONFIG = DeployConfig(...)`) also sets:
  - `PROXY_ENABLED="true"`
  - `PROXY_CAPTIONS_ONLY="true"`
  - `PROXY_POOL=...`

Why it can look split:
0. How the worker still sees proxy settings even when local export is blocked:
   - `deploy_config.py` is still the source of truth.
   - `load_python_env.py` blocks only global `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` exports to the **local deploy shell**, but it still exports worker-control vars like `PROXY_ENABLED`, `PROXY_CAPTIONS_ONLY`, and `PROXY_POOL`.
   - `deploy_worker_vm.sh` writes those values into `/etc/capstone/worker.env` on the VM (including `PROXY_POOL` when proxy mode is enabled).
   - `backend/proxy_egress.py` reads proxy endpoints from `PROXY_POOL` first, then falls back to `ALL_PROXY` / `HTTPS_PROXY` / `HTTP_PROXY` if present.
   - Net: worker proxy behavior comes from `PROXY_POOL` + proxy flags in worker env, not from auto-exported global proxy vars in your local shell.
   - Where proxy env vars are exported/written to:
     - Local machine deploy shell (current bash session): values are exported by `infra/gcp/load_python_env.sh` via `eval` of `load_python_env.py` output.
     - Worker VM runtime file: `infra/gcp/deploy_worker_vm.sh` writes proxy settings to `/etc/capstone/worker.env`.
     - Worker process environment: systemd loads `EnvironmentFile=/etc/capstone/worker.env` in `capstone-worker.service`, so `backend/proxy_egress.py` sees them at runtime.
     - Not auto-exported destinations: global `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` are not auto-exported to local deploy shells and are not written into worker env by default.
     - What "by default" means here:
       - A normal run of the repo's deploy scripts, with no manual customization, does **not** add `HTTP_PROXY=...`, `HTTPS_PROXY=...`, or `ALL_PROXY=...` lines to `/etc/capstone/worker.env`.
       - So after a standard deploy, those three global proxy vars are unset in the worker process unless you explicitly add them yourself (for example via manual env-file edits or script changes).
1. `load_python_env.py` now **intentionally blocks exporting** global `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` to local deploy shells.
   - Point of `load_python_env.py`:
     - It is the bridge between `infra/gcp/deploy_config.py` and bash deploy scripts.
     - It reads deploy config values and prints safe `export ...` lines that `infra/gcp/load_python_env.sh` can `eval`.
     - It only exports a key if that key is not already set in your shell, so manual overrides still win.
     - It centralizes filtering rules (like blocking global proxy vars) so every deploy script gets the same env-loading behavior.
   - What "local deploy shells" means here:
     - The shell session on the machine that starts deploy scripts (for example WSL/bash on your laptop running `infra/gcp/deploy_all.sh`, `deploy_api.sh`, `deploy_web.sh`, or `deploy_worker_vm.sh`).
     - This is where `infra/gcp/load_python_env.sh` imports values from `deploy_config.py`.
     - It does **not** mean the worker VM runtime environment (`/etc/capstone/worker.env`) or app-process-only proxy routing in backend code.
   - Why this is blocked: local deploy shells run mixed commands (`gcloud`, `docker`, auth, metadata calls). Auto-exporting global proxy vars there can route unrelated traffic through residential proxies and cause noisy failures or unnecessary paid-proxy usage.
   - Where this is blocked in code: `infra/gcp/load_python_env.py` (`BLOCKED_EXPORT_KEYS`).
   - Where it is **not** blocked:
     - `infra/gcp/deploy_config.py` still stores/resolves `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`.
     - `infra/gcp/env.example.sh` still shows manual `export HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` usage.
     - `backend/proxy_egress.py` still reads `ALL_PROXY` / `HTTPS_PROXY` / `HTTP_PROXY` at runtime when present.
2. `deploy_worker_vm.sh` also **intentionally does not write** global `HTTP(S)_PROXY/ALL_PROXY` into worker env.
3. The "captions-only" behavior is enforced in runtime code (`backend/proxy_egress.py`), where `PROXY_CAPTIONS_ONLY=true` disables proxy usage for non-caption purposes.

So the config is centralized in `deploy_config.py`, but usage is intentionally scoped in runtime/deploy scripts to avoid unnecessary proxying.
