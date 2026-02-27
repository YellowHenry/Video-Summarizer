# Proxy Egress Guide (VM Worker)

## Current model (implemented)

Proxy configuration for app runtime is now driven by app-specific fields:

- `PROXY_ENABLED`
- `PROXY_CAPTIONS_ONLY`
- `PROXY_POOL`
- `PROXY_ROTATION_MODE`
- `PROXY_MAX_RETRIES`
- `PROXY_BACKOFF_SECONDS`
- optional: `PROXY_AUTOGENERATE*`

Global proxy env vars (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) are no longer app/deploy configuration inputs.

## Runtime behavior

- Caption-fetch path can use proxy egress.
- With `PROXY_CAPTIONS_ONLY=true`, non-caption paths stay direct:
  - YouTube audio/media fallback download
  - GCS artifact uploads
  - OpenAI traffic (unless separately overridden)

Proxy endpoint source order in runtime:
1. `PROXY_POOL` (preferred)
2. `YTDLP_PROXY` (legacy fallback only)

## Deploy behavior

- `infra/gcp/deploy_config.py` is the source of truth.
- `infra/gcp/deploy_worker_vm.sh` writes `PROXY_*` settings into worker env.
- `infra/gcp/load_python_env.py` exports deploy config values (without special-casing removed global proxy vars).
- Infra scripts keep a defensive `gcloud()` wrapper that unsets `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` before control-plane calls.  
  This is safety isolation for local deploys, not app proxy support.

## Recommended config example

```python
PROXY_ENABLED="true"
PROXY_CAPTIONS_ONLY="true"
PROXY_POOL="http://<username>:<password>@p.webshare.io:80"
PROXY_ROTATION_MODE="on_rate_limit"
PROXY_MAX_RETRIES="3"
PROXY_BACKOFF_SECONDS="2"
```

Optional auto-generation:

```python
PROXY_AUTOGENERATE="true"
PROXY_AUTOGENERATE_TEMPLATE="http://user:pass@proxy{i}.provider.net:80{i}"
PROXY_AUTOGENERATE_START="1"
PROXY_AUTOGENERATE_END="3"
```

## Validation checklist

1. Redeploy worker:
```bash
bash infra/gcp/deploy_worker_vm.sh
```

2. Confirm worker env has `PROXY_*` values and no global proxy vars:
```bash
gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "sudo grep -E '^(PROXY_|YTDLP_PROXY|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY)=' /etc/capstone/worker.env || true"
```

3. Submit a caption-first YouTube job and check worker logs for caption egress entries.

## Troubleshooting

- If captions still 429 often:
  - verify proxy endpoint credentials
  - increase `PROXY_MAX_RETRIES`
  - consider better-quality rotating residential endpoints
- If non-caption traffic seems proxied unexpectedly:
  - verify `PROXY_CAPTIONS_ONLY=true`
  - check worker env for accidental global proxy vars
