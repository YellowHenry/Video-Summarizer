# YouLearn Audio Summarizer

YouLearn is a Google-authenticated web app for turning long YouTube videos or uploaded audio into private AI summaries, transcripts, searchable notes, job chat, and optional email digests. The processing pipeline is intentionally visible: each job moves through `queued`, `downloading`, `preprocessing`, `summarizing`, and `complete` or `failed`, so reviewers and users can understand what the worker is doing.

This repo now supports both:
- Desktop app (`Tkinter`, legacy path)
- Web app migration (`FastAPI + React + worker queue`)

The web stack keeps the existing processing modules (`downloader`, `summarizer`, `qa`, `vector_store`) from the legacy python application and wraps them behind APIs.

## Live Google Cloud Deployment

- Web app: [https://audio-summarizer-web-tedb4icw5q-uc.a.run.app](https://audio-summarizer-web-tedb4icw5q-uc.a.run.app)
- API service: [https://audio-summarizer-api-tedb4icw5q-uc.a.run.app](https://audio-summarizer-api-tedb4icw5q-uc.a.run.app)

## Tkinter Desktop App (Legacy)

The original desktop workflow is still available.

- Entry point: `app.py`
- Run:
```bash
python app.py
```
- Uses the original backend modules (`backend/jobs.py`, `backend/downloader.py`, `backend/summarizer.py`, etc.)
- Stores results under `storage/<job_id>/`

Use this path if you want the local Tkinter UI instead of the web app.

## Web Architecture

- Frontend: `web/` (`Vite + React + TypeScript`)
- API: `backend/webapp/api.py` (FastAPI)
- Worker: `backend/webapp/worker.py` (RQ worker)
- Queue: Redis (`rq`)
- Metadata DB: SQLAlchemy models in `backend/webapp/models.py`:
  - `jobs`
  - `chat_messages`
  - `job_artifacts`
  - `vectors`
- Auth: Google Sign-In (`/api/auth/me` + bearer token on all data endpoints)
- Multi-profile isolation: jobs/artifacts/chat/search are scoped by signed-in Google account
- Optional per-user email digests:
  - opt-in daily or weekly delivery to the signed-in Google email
  - first real digest can include older completed jobs from before enable time
  - later digests are incremental based on the last successful digest window
  - includes a rolling AI-generated taste/profile summary built from the user's completed jobs, including historical ones
  - uses Cloud Scheduler -> internal API sweep -> VM worker delivery path
- Artifact storage:
  - Local (`storage/objects`) by default
  - GCS when `OBJECT_STORAGE_BACKEND=gcs` and `GCS_BUCKET` is set
- Vector search:
  - Current query path: DB-backed vectors (`vectors` table) when available
  - On Postgres with pgvector enabled, search uses native vector distance in SQL (`embedding_vector <=> query`)
  - Fallback query path: `storage/index/index.json` via `backend/vector_store.py`
  - Optional pgvector migration: `python -m backend.webapp.migrate` with `WEBAPP_ENABLE_PGVECTOR=true`
- Service container / DI: `backend/webapp/services.py`
- Metadata adapter: `backend/webapp/metadata_store.py`
- Key-redacting logging: `backend/webapp/logging_config.py`

## Web API Endpoints

- `POST /api/uploads/presign`
- `PUT /api/uploads/{object_key}` (local object-store fallback upload)
- `GET /api/auth/me`
- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/summary`
- `GET /api/jobs/{job_id}/transcript`
- `GET /api/jobs/{job_id}/chat`
- `POST /api/jobs/{job_id}/chat`
- `POST /api/search`
- `GET /api/digests/settings`
- `PUT /api/digests/settings`
- `GET /api/digests/history`
- `POST /internal/digests/sweep` (internal scheduler trigger)

`POST /api/jobs` returns:
- `job_id`
- `status`
- `created_at`
- `updated_at`

## Local Run (No Docker)

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set env vars:
```bash
# Required for real transcription/summarization/embeddings
set OPENAI_API_KEY=replace-with-openai-key

# Required for Google login (backend verifier + frontend client)
set WEBAPP_GOOGLE_CLIENT_ID=your-google-oauth-client-id.apps.googleusercontent.com

# Required for queued background jobs (unless WEBAPP_SYNC_JOBS=true)
set REDIS_URL=redis://localhost:6379/0
```

3. Start API:
```bash
uvicorn backend.webapp:app --reload --port 8000
```

4. Start worker (second terminal):
```bash
python -m backend.webapp.worker
```

5. Start frontend:
```bash
cd web
npm install
set VITE_GOOGLE_CLIENT_ID=your-google-oauth-client-id.apps.googleusercontent.com
npm run dev
```

Frontend default URL: `http://localhost:5173`  
API default URL: `http://localhost:8000`

## Demo Path

1. Sign in with Google.
2. Submit a YouTube URL.
3. Watch the Job Detail timeline move through the processing stages.
4. Open a completed job and review Summary, Transcript, and Job Chat.
5. Ask a follow-up question in Job Chat.
6. Use Global Search to ask across saved transcripts.
7. Open Email Digests to show opt-in daily/weekly recap settings and rolling profile preview.

Failed jobs are intentionally reviewer-friendly: the main panel gives plain-English failure copy and a `Try again` action, while raw provider/yt-dlp detail stays under Debug info.

### Optional: run without Redis/worker

For local debugging only:
```bash
set WEBAPP_SYNC_JOBS=true
```

In this mode, `POST /api/jobs` processes inline in the API process.

### Run migrations

```bash
python -m backend.webapp.migrate
```

This creates tables and (for Postgres) optionally enables pgvector + vector index.

## Docker Compose (Local Full Stack)

```bash
docker compose up --build
```

Services:
- API: `http://localhost:8000`
- Web: `http://localhost:5173`
- Redis: `localhost:6379`
- Postgres: `localhost:5432`

## Backfill Existing Desktop Storage

Import previous `storage/<job_id>/...` content into the web metadata store + object store:

```bash
python -m backend.webapp.backfill_from_storage
# optional custom storage root
python -m backend.webapp.backfill_from_storage --storage-root "C:/path/to/storage"
```

This imports:
- `meta.json`
- `summary.txt`
- `transcript.txt`
- `title.txt`
- `chat.json`

And re-indexes vectors when `OPENAI_API_KEY` is set.
If you want metadata/artifact backfill only (no embedding cost), run without `OPENAI_API_KEY`.

## Tests

Python tests (unit + integration):

```bash
python -m pytest -q
```

Covers:
- job creation/enqueue behavior
- worker state transition and artifact persistence
- chat persistence round-trip
- upload -> summarize -> summary/transcript retrieval
- global search chunk links

Playwright E2E scaffolding:

```bash
cd web
npm install
npm run test:e2e
```

Files:
- `web/playwright.config.ts`
- `web/tests/e2e/app.spec.ts`

## Google Cloud Deployment Notes

This repo includes container definitions:
- `Dockerfile.api`
- `Dockerfile.worker`
- `web/Dockerfile`

Recommended managed services:
- Cloud Run (`api` + optional `web`)
- Persistent Compute Engine VM for worker (desktop-like Chrome cookies)
- Cloud SQL Postgres
- Redis queue (`Memorystore` or Redis on the worker VM for cost-optimized mode)
- GCS bucket for artifacts

First-time local auth:

```powershell
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

Set these env vars in Cloud Run:
- `OPENAI_API_KEY`
- `DATABASE_URL`
- `REDIS_URL`
- `RQ_QUEUE_NAME`
- `RQ_RETRY_MAX` (default `3`)
- `RQ_RETRY_INTERVALS` (comma-separated seconds, default `30,120,300`)
- `OBJECT_STORAGE_BACKEND=gcs`
- `GCS_BUCKET`
- `API_BASE_URL`
- `CORS_ALLOW_ORIGINS`
- `WEB_APP_BASE_URL`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `DIGEST_SWEEP_SECRET`
- `DIGEST_SWEEP_INTERVAL_MINUTES`
- `DIGEST_PROFILE_MAX_JOBS`
- `DIGEST_MAX_ITEMS_PER_EMAIL`
- `DIGEST_JOB_EXCERPT_CHARS`
- `DIGEST_SEND_HOUR_LOCAL`
- `DIGEST_WEEKLY_WEEKDAY`

Automated deployment scripts are under `infra/gcp/`:
- `infra/gcp/bootstrap.sh`
- `infra/gcp/build_and_push.sh`
- `infra/gcp/deploy_api.sh`
- `infra/gcp/provision_worker_vm.sh` (persistent VM worker provisioning)
- `infra/gcp/deploy_worker_vm.sh` (deploy/update worker on VM)
- `infra/gcp/provision_worker_vm.ps1` (Windows wrapper)
- `infra/gcp/deploy_worker_vm.ps1` (Windows wrapper)
- `infra/gcp/deploy_web.sh` (optional frontend service)
- `infra/gcp/map_domain.sh` (optional domain + HTTPS mapping)
- `infra/gcp/deploy_all.sh` (one-command orchestrator)
- `infra/gcp/deploy_all.ps1` (Windows wrapper)
- `infra/gcp/validate_deploy.sh` (post-deploy checks + optional smoke)
- `infra/gcp/validate_deploy.ps1` (Windows wrapper)
- `infra/gcp/rightsize_online_costs.sh` (Cloud SQL right-sizing helper with fallback)
- `infra/gcp/run_backfill_with_cloudsql_proxy.sh` (local-to-CloudSQL backfill helper)
- `infra/gcp/run_backfill_with_cloudsql_proxy.ps1` (Windows wrapper)
- `infra/gcp/env.example.sh` (env template)

### YouTube rate-limit mitigation (Webshare + captions-only proxy mode)

The web app worker can use a rotating proxy endpoint (for example Webshare) to reduce YouTube caption rate-limit failures.

Current VM-worker behavior supports a split path:
- Caption-fetch path can use proxy egress.
- Whisper fallback path (audio download/extraction + OpenAI calls) can stay direct.

Key env/config controls:
- `PROXY_ENABLED="true"` enables proxy logic.
- `PROXY_CAPTIONS_ONLY="true"` proxies caption requests only; keeps audio download direct.
- `PROXY_POOL` sets the proxy endpoint(s).
- `PROXY_MAX_RETRIES`, `PROXY_ROTATION_MODE`, `PROXY_BACKOFF_SECONDS` control retry/rotation.
- `OPENAI_TRUST_ENV_PROXY="false"` keeps OpenAI traffic off proxy.

Example rotating endpoint:
- `PROXY_POOL="http://<username>:<password>@p.webshare.io:80"`

See `infra/gcp/README.md` for required env vars, VPC connector setup, service-account IAM, and deployment sequence.

### Email digests

Signed-in users can opt into daily or weekly email digests from the web UI.

Digest behavior:
- recipient is always the signed-in Google email
- daily and weekly are the two supported cadences
- send time is fixed at `8:00 AM` in the saved local timezone
- first real digest can include historical completed jobs from before enable time
- rolling profile already uses historical completed jobs
- after the first successful digest, later digests are incremental from the last successful digest window
- empty windows do not send email
- digest emails deep-link back into the SPA with `?job=<job_id>&tab=summary`

Operational requirements:
- SMTP must be configured
- `WEB_APP_BASE_URL` must point at the frontend origin used in email links
- Cloud Scheduler must be enabled so it can call `/internal/digests/sweep`

### Online Cost-Optimized Mode (keeps app on, lowers idle cost)

If you want lower daily cost without "turning the app off", the repo now supports a VM-worker cost mode:
- Keep API/web on Cloud Run and worker on the persistent VM.
- Run Redis on the worker VM (`REDIS_RUNTIME="worker_vm"`) instead of Memorystore.
- Downsize Cloud SQL to a shared-core tier (target `SQL_TIER="db-f1-micro"`, fallback `db-g1-small` via helper script).
- Downsize the worker VM to `e2-small` and shrink the boot disk to `32 GB` for the current low-risk cost floor.

Key config/env knobs:
- `REDIS_RUNTIME` = `memorystore` or `worker_vm`
- `REDIS_VM_PORT`
- `REDIS_VM_REQUIREPASS` (required when `REDIS_RUNTIME="worker_vm"`)
- `REDIS_VM_FIREWALL_RULE`
- `REDIS_VM_ALLOWED_SOURCE` (defaults to `VPC_CONNECTOR_RANGE`)
- `SQL_TIER` (target tier for `infra/gcp/rightsize_online_costs.sh`)
- optional `SQL_STORAGE_TYPE` (for example `HDD`, if acceptable)
- `WORKER_VM_MACHINE_TYPE` (recommended low-risk target: `e2-small`)
- `WORKER_VM_DISK_SIZE_GB` (recommended low-risk target: `32`)

Why this helps:
- Managed Redis + always-on Cloud SQL are the main idle cost drivers in low-traffic usage.
- VM Redis removes Memorystore cost while preserving queue behavior (`rq`).

Tradeoffs:
- VM Redis is cheaper but less managed than Memorystore (you own VM Redis uptime/config).
- Shared-core Cloud SQL is slower than custom tiers, but often fine for small personal traffic.
- `infra/gcp/pause_stack.sh` / `resume_stack.sh` still exist as optional "idle mode" controls; they are not required for this online cost-reduction mode.
- After VM downsizing + shared-core SQL + VM Redis, the likely remaining always-on floor is the Serverless VPC connector used by Cloud Run API -> VM Redis. If a 72-hour average still stays above roughly `$0.90/day`, the next savings step is an architecture change rather than another config tweak.
- Note: GCE boot disks cannot be shrunk in place. `WORKER_VM_DISK_SIZE_GB="32"` applies automatically to future VM creations; reducing an existing `64 GB` worker disk requires a rebuild/recreate step if you want that savings live.
- Note: Cloud SQL storage type is often immutable after creation. If your existing instance rejects an `SQL_STORAGE_TYPE="HDD"` patch, keep the shared-core tier and treat disk-type savings as a recreate-time option rather than an in-place optimization.

## What Is Still Manual (By Design)

- YouTube Chrome login on the worker VM:
  - Required first-time, and occasionally again if cookies/session expire or YouTube re-challenges.
- Local auth/tools health on your PC:
  - Keep `gcloud` logged in:
    - deploy scripts run many `gcloud ...` commands from your machine (enable APIs, deploy Cloud Run, configure IAM, update VM, etc.).
  - Keep Docker Desktop running/healthy:
    - these scripts build images locally from your `Dockerfile.api` / `Dockerfile.worker` / `web/Dockerfile`, then push to Artifact Registry.
    - Cloud Run/VM then pull those pushed images from Artifact Registry.
    - On Windows, `docker build`/`docker push` talk to the local Docker daemon provided by Docker Desktop (`dockerDesktopLinuxEngine`).
    - If Docker Desktop is closed or stuck, there is no daemon to execute builds/pushes, so deploy stops before images reach Artifact Registry.
    - Typical failure symptom is: `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`.
- Optional custom domain DNS:
- Optional custom domain DNS:
  - Needed only if you set `API_DOMAIN` / `WEB_DOMAIN`.
- Optional historical-job backfill:
  - Needed only if you want old local jobs to appear online.

## Security

- OpenAI key is server-side only (`OPENAI_API_KEY` env var)
- Repo-wide fallback key source is `backend/config.py` `OPENAI_API_KEY` (used when env var is absent)
- Never expose API keys in frontend code
- If running public/no-login, add request limits and abuse controls before sharing widely

## Git Hygiene

- This repo ignores local/runtime artifacts by default (for example: `web/node_modules`, `web/dist`, Python caches, logs, `storage/`, and local `.env` files).
