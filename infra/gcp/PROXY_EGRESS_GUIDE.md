# Proxy Egress Guidance (VM Worker)

## Long-term reliable approach

Route worker traffic through proxy egress using standard environment variables in the VM worker environment:

- `HTTP_PROXY`
- `HTTPS_PROXY`
- `ALL_PROXY`
- `NO_PROXY` (minimum: `169.254.169.254,metadata.google.internal,localhost,127.0.0.1`)

After setting these, restart the worker so the new env is applied.

Use uppercase env-style names in `infra/gcp/deploy_config.py` so deployment wiring and runtime expectations stay consistent.

## Current behavior (implemented)

This repository now has first-class proxy wiring end-to-end:

- `infra/gcp/deploy_config.py` is the source of truth for proxy settings.
- `infra/gcp/deploy_worker_vm.sh` writes proxy env vars into `/etc/capstone/worker.env` on deploy.
- Worker runtime (`backend/downloader.py` + `backend/summarizer.py`) applies proxy settings to:
  - `yt-dlp` calls
  - `requests` calls
  - with optional scope control via `PROXY_CAPTIONS_ONLY=true` (caption fetches proxied, media/audio download direct)
- Rate-limit retry/backoff + proxy rotation is implemented via `backend/proxy_egress.py`.

`YTDLP_PROXY` is still accepted as a fallback input, but only when proxy mode is enabled. Prefer standard env vars (`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`) plus `PROXY_POOL`.

## Auto-generation support (implemented)

You can auto-generate proxy endpoints instead of manually listing every URL.

Config fields:

- `PROXY_AUTOGENERATE=true`
- `PROXY_AUTOGENERATE_TEMPLATE` (supports `{i}`, `{index}`, or `{n}`)
- `PROXY_AUTOGENERATE_START`
- `PROXY_AUTOGENERATE_END`

Example template:

```python
PROXY_ENABLED="true"
PROXY_AUTOGENERATE="true"
PROXY_AUTOGENERATE_TEMPLATE="http://user:pass@proxy{i}.provider.net:80{i}"
PROXY_AUTOGENERATE_START="1"
PROXY_AUTOGENERATE_END="3"
```

This expands into `PROXY_POOL` and auto-fills primary `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` if they are unset.

Important: this only auto-generates URLs from your provider pattern. It does not provision a proxy service for you.

## Implementation plan: proxy support for rate limits

Use this as a practical checklist to move from manual proxy setup to reliable proxy-based egress that reduces rate-limit errors.

### 1) Add config fields (source of truth)

In `infra/gcp/deploy_config.py`, add proxy settings the deploy scripts can read:

- Use uppercase env-style field names only (match existing config style).
- `PROXY_ENABLED` (`true`/`false`)
- `PROXY_CAPTIONS_ONLY` (`true`/`false`) to proxy captions only
- `HTTP_PROXY`
- `HTTPS_PROXY`
- `ALL_PROXY`
- `NO_PROXY` (include at least: `169.254.169.254,metadata.google.internal,localhost,127.0.0.1`)
- Optional advanced fields (still uppercase style), such as:
  - `PROXY_ROTATION_MODE` (`none`, `per_job`, `on_rate_limit`)
  - `PROXY_MAX_RETRIES`
  - `PROXY_BACKOFF_SECONDS`
  - `PROXY_POOL` (optional comma-separated list of proxy URLs used for rotation)

Goal: all proxy behavior is controlled in one place, not by ad-hoc VM edits.

How to choose proxies for rate-limit reduction:

- Prefer paid, reliable proxies (public/free proxies are usually unstable and quickly blocked).
- For YouTube-heavy traffic, residential or ISP-backed proxies are usually more resilient than basic datacenter proxies.
- Start with a small pool (for example 3-5 proxies) from different IP ranges/regions to avoid all traffic sharing one identity.
- Run a short canary test batch against each candidate and keep only proxies with low `429` rate and acceptable latency.
- Put the best proxy as primary (`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`) and keep the rest in `PROXY_POOL` for rotation.

### 2) Wire config into VM deployment

In `infra/gcp/deploy_worker_vm.sh`, write proxy env vars into the worker runtime environment (for example, a systemd env file), then restart the worker:

- If `PROXY_ENABLED=true`: set `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`
- If `PROXY_ENABLED=false`: do not set proxy env vars (rollback behavior)
- Ensure values persist across redeploys and reboots

Goal: new VMs and redeployed VMs automatically get the same proxy settings.

### 3) Make runtime code proxy-aware

Update worker code so both network paths use the same proxy strategy:

- `requests`: pass a `proxies` dict (or rely on env consistently)
- `yt-dlp`: pass `--proxy` (or equivalent API option) from the selected proxy

Important: do not rely on `YTDLP_PROXY` only. Keep standard env vars as baseline compatibility.

Goal: every outbound request path is explicitly proxy-capable.

### 4) Add rate-limit handling + proxy rotation

When a request fails due to rate limiting (for example HTTP `429` or known `yt-dlp` rate-limit errors):

- Treat `429` as transient and retry with bounded exponential backoff (for example 2s, 4s, 8s)
- Switch to the next proxy if `PROXY_ROTATION_MODE` allows it
- If all configured proxies fail, fall back to the existing media + Whisper path (instead of retrying forever)
- Stop after a max retry count and return a clear error

Goal: transient rate limits recover automatically without endless retries.

### 5) Add safe logging and metrics

Track behavior without leaking secrets:

- Log proxy alias/index, not full proxy URL with credentials
- Count: total attempts, retries, proxy switches, rate-limit failures
- Add a simple success-rate metric before/after proxy rollout

Goal: verify improvements and debug failures safely.

### 6) Roll out in stages (single-VM setup today)

Current deploy config is single worker VM (`WORKER_VM_NAME`), so stage the rollout on that same VM:

Phase A (low risk, deploy-only):
1. Add uppercase proxy fields to `infra/gcp/deploy_config.py` (single source of truth).
2. Update `infra/gcp/deploy_worker_vm.sh` to write `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY` into worker env.
3. Redeploy worker and restart service.
4. Verify worker health and that env vars are present.
5. Keep setup simple: for now, store these values directly in `infra/gcp/deploy_config.py`.

Phase B (code changes):
1. Add explicit proxy wiring in runtime code (`yt-dlp` + `requests`).
2. Add bounded retry/backoff for `429` and related transient rate-limit errors.
3. Add proxy rotation if configured, then fallback to existing media + Whisper path if proxies are exhausted.
4. Decision rule: if canary metrics seem fine with proxy mode, keep `PROXY_ENABLED=true` as the default.

Rollback toggle (what this means):
- Set `PROXY_ENABLED=false` and redeploy worker.
- Deployment should stop injecting proxy env vars (or set them empty), restart worker, and return outbound traffic to direct egress.
- This is a fast safety switch if proxy provider issues, auth issues, or unexpected failures appear.

If you later run multiple worker VMs, apply the same canary pattern to one VM first, then expand.

Goal: reduce risk while introducing proxy logic.

### 7) Definition of done

Proxy support is done when all are true:

- Proxy config is managed in deploy code (not manual only)
- Uppercase proxy fields are used consistently (`PROXY_ENABLED`, `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`)
- `requests` and `yt-dlp` both use the configured proxy path
- Rate-limit retries/rotation are implemented and bounded
