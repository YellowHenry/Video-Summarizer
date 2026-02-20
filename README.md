# Audio Summarizer

This repo now supports both:
- Desktop app (`Tkinter`, legacy path)
- Web app migration (`FastAPI + React + worker queue`)

The web stack keeps your existing processing modules (`downloader`, `summarizer`, `qa`, `vector_store`) and wraps them behind APIs.

## Tkinter Desktop App (Legacy, Still Supported)

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
- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/summary`
- `GET /api/jobs/{job_id}/transcript`
- `GET /api/jobs/{job_id}/chat`
- `POST /api/jobs/{job_id}/chat`
- `POST /api/search`

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
set OPENAI_API_KEY=sk-...

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
npm run dev
```

Frontend default URL: `http://localhost:5173`  
API default URL: `http://localhost:8000`

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
- Memorystore Redis
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
- `infra/gcp/run_backfill_with_cloudsql_proxy.sh` (local-to-CloudSQL backfill helper)
- `infra/gcp/run_backfill_with_cloudsql_proxy.ps1` (Windows wrapper)
- `infra/gcp/env.example.sh` (env template)

See `infra/gcp/README.md` for required env vars, VPC connector setup, service-account IAM, and deployment sequence.

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
- `infra/gcp/env.sh` is intentionally ignored so machine-specific deploy values and secrets are not committed.