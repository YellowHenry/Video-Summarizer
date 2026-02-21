# GCP Deployment (Cloud Run + Cloud SQL + Redis + GCS)

This folder contains deployment scripts for the web migration stack:
- API service on Cloud Run
- Worker service on persistent Compute Engine VM
- Optional web frontend service on Cloud Run
- Cloud SQL Postgres
- Memorystore Redis
- GCS artifact bucket
- Optional Cloud Run domain mapping (managed HTTPS)

## Prerequisites

- `gcloud` CLI installed/authenticated
- `docker` installed (for image builds in these scripts)
- `bash` shell available (`Git Bash` or `WSL`) for `.sh` scripts
- Billing enabled
- A project with permissions to create Cloud Run, Cloud SQL, Redis, GCS, IAM resources
- OpenAI API key
- Optional for cloud backfill from local storage: `cloud-sql-proxy`

### How to meet these prerequisites (beginner setup)

1. Install Google Cloud CLI (`gcloud`)
```powershell
winget install Google.CloudSDK
gcloud version
```
If `gcloud version` prints a version, this prerequisite is done.

2. Install Docker Desktop
- Install Docker Desktop from Docker's installer for Windows.
- Start Docker Desktop and wait until it says Docker is running.
```powershell
docker --version
docker info
```
If `docker info` works, Docker is ready.

3. Install a Bash shell (`Git Bash` or `WSL`)
- Easiest: install Git for Windows and use Git Bash.
```powershell
winget install Git.Git
```
- Alternative: install WSL.
```powershell
wsl --install
```
Verify one of these works:
```powershell
bash --version
```

If you see this error after running:
```powershell
PS C:\Users\danmc\Documents\capstone> bash --version
```

Error output:
```text
<3>WSL (...) ERROR: CreateProcessCommon:... execvpe(/bin/bash) failed: No such file or directory
```
it means Windows is trying to use WSL for `bash`, but no Linux distro/shell is installed yet.

If Ubuntu is installed but you still get this error, your **default** WSL distro is likely `docker-desktop` (which does not provide normal shell tools for this use case).  
This is what we did to fix it:

```powershell
# 1) Check current default distro
wsl --status
wsl --list --verbose

# 2) Confirm Ubuntu has bash
wsl -d Ubuntu -e bash --version

# 3) Set Ubuntu as the default distro
wsl -s Ubuntu

# 4) Retry generic bash call
wsl -e bash --version
```

Fix option A (recommended): install and initialize a WSL distro
```powershell
wsl --list --verbose
wsl --install -d Ubuntu
```
Then open Ubuntu once from Start Menu so setup completes, and run:
```powershell
wsl -e bash --version
```

Fix option B: use Git Bash instead of WSL
- Install Git for Windows (`winget install Git.Git`)
- Open the **Git Bash** app and run:
```bash
bash --version
```
- From PowerShell, you can also test Git Bash directly:
```powershell
"C:\Program Files\Git\bin\bash.exe" --version
```

4. Create/select a GCP project and enable billing
- In Google Cloud Console:
  - Create a project (or select an existing one).
  - Attach a billing account to that project.
- Then set it locally:
```powershell
gcloud config set project YOUR_PROJECT_ID
```

5. Ensure your account has enough permissions
- Simplest for personal projects: `Owner` on the project.
- Minimum practical roles must allow creating Cloud Run, Cloud SQL, Redis, GCS, Artifact Registry, IAM bindings, and Service Accounts.

How to check this:

```powershell
# Confirm which account gcloud is using
gcloud auth list

# Confirm the active project
gcloud config get-value project
```

If `gcloud auth list` shows:
```text
No credentialed accounts.
```
fix it with:
```powershell
# Sign in to Google Cloud in your browser
gcloud auth login

# Also set Application Default Credentials (used by local tooling/scripts)
gcloud auth application-default login

# Set your active project
gcloud config set project YOUR_PROJECT_ID

# Re-check
gcloud auth list
gcloud config get-value project
```

Then check IAM roles in Cloud Console:
- Go to `IAM & Admin` -> `IAM`
- Find your signed-in account
- Verify you have either:
  - `Owner` (simplest for personal projects), or
  - a custom set of roles that covers Cloud Run, Cloud SQL, Redis, GCS, Artifact Registry, Service Account Admin, and IAM policy updates

Optional CLI check of your role bindings (project-level):
```powershell
gcloud projects get-iam-policy YOUR_PROJECT_ID `
  --flatten="bindings[].members" `
  --filter="bindings.members:user:YOUR_EMAIL" `
  --format="table(bindings.role)"
```

If you do not see enough roles, ask the project owner/admin to grant them in:
- `IAM & Admin` -> `IAM` -> `Grant Access`

6. Create an OpenAI API key
- Generate a key in your OpenAI account.
- Put it in one place: `backend/config.py` -> `OPENAI_API_KEY = "sk-..."`.
- You can still use shell env `OPENAI_API_KEY`; env values override the file when set.
- Never put it in frontend code.

7. Optional: install `cloud-sql-proxy` (only needed for local backfill to Cloud SQL)
- What this is: a local tunnel from your laptop to your Cloud SQL instance.
- When you need it: only when running DB work from your local machine (for example `run_backfill_with_cloudsql_proxy.sh`).
- When you do not need it: normal Cloud Run deploy/use, where API/worker connect directly in GCP.
- Install on Windows (PowerShell):
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\bin" | Out-Null
Invoke-WebRequest -Uri "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.21.1/cloud-sql-proxy.x64.exe" -OutFile "$env:USERPROFILE\bin\cloud-sql-proxy.exe"
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";$env:USERPROFILE\bin", "User")
```
- Why this URL: it is the official Cloud SQL Proxy binary bucket and works without needing admin rights to modify `gcloud` components.
- After running those commands, close and reopen PowerShell so the updated `PATH` is loaded.
- Verify:
```powershell
cloud-sql-proxy --version
where.exe cloud-sql-proxy
Get-Command cloud-sql-proxy
```
- If PowerShell still says command not found, run it once with full path:
```powershell
& "$env:USERPROFILE\bin\cloud-sql-proxy.exe" --version
```

## First-time local auth (from your PC)

```powershell
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

Then choose one config method:
- Recommended: set values in `infra/gcp/deploy_config.py` (no long export block needed).
- Alternative: export shell env vars in Git Bash/WSL.

## Configure Values In One Python File (Recommended)

All `infra/gcp/*.sh` scripts now auto-load values from:
- `infra/gcp/deploy_config.py`

Edit `infra/gcp/deploy_config.py` and set the required fields:
- `PROJECT_ID`
- `REPO`
- `DB_PASSWORD`
- `BUCKET_NAME`
- `OPENAI_API_KEY` (or keep this unset and rely on `backend/config.py` key)

You can keep defaults for most other fields (`REGION`, image names, service names, etc.) unless you want custom names.

### How to set each required field correctly (beginner guide)

1. `PROJECT_ID`
- This must be your real Google Cloud project ID (not the display name).
- Check your current project:
```powershell
gcloud config get-value project
```
- If this prints `(unset)` or the wrong project, set it:
```powershell
gcloud config set project YOUR_PROJECT_ID
```

2. `REPO`
- This is the Artifact Registry repository name used to store Docker images.
- Use lowercase letters, numbers, and dashes only.
- If you do not care about naming, keep: `capstone-repo` (the scripts can create it).

3. `DB_PASSWORD`
- This is the password the app uses for the Cloud SQL Postgres user.
- Pick a strong password and avoid quotes/newlines.
- Quick way to generate one in PowerShell:
```powershell
[guid]::NewGuid().ToString("N")
```

4. `BUCKET_NAME`
- This is the GCS bucket for transcripts/summaries/artifacts.
- Bucket names must be globally unique, lowercase, and usually include your project ID.
- Good pattern:
```text
your-project-id-capstone-artifacts
```

5. `OPENAI_API_KEY`
- Option A: put it in `infra/gcp/deploy_config.py` as `OPENAI_API_KEY="sk-..."`.
- Option B: leave it unset here and put it in `backend/config.py` (`OPENAI_API_KEY`).
- Option C: export `OPENAI_API_KEY` in shell for a one-off override.

### Minimal safe config to paste

```python
# infra/gcp/deploy_config.py
CONFIG = DeployConfig(
    PROJECT_ID="video-summarizer-487915",
    REPO="capstone-repo",
    DB_PASSWORD="1e344de5940d4378ac21fc80ae03dccb",
    BUCKET_NAME="video-summarizer-487915-capstone-artifacts",
    OPENAI_API_KEY=None,  # uses backend/config.py fallback
)
```

### Verify your Python config is being read

Before deploy, run:
```powershell
python infra/gcp/load_python_env.py
```
You should see exported values (including your `PROJECT_ID`, `REPO`, `BUCKET_NAME`).

You can also verify inside Bash:
```powershell
bash -lc 'cd /mnt/c/Users/danmc/Documents/capstone; source infra/gcp/load_python_env.sh; declare -p PROJECT_ID'
```
If it prints:
```text
declare -x PROJECT_ID="tribal-primer-438802-n0"
```
that means your `infra/gcp/deploy_config.py` value is being loaded correctly.

Important:
- This does **not** change global gcloud config by itself.
- `gcloud config get-value project` may still show another project until deploy preflight runs `gcloud config set project "${PROJECT_ID}"` (or you set it manually).

Example:
```python
# infra/gcp/deploy_config.py
CONFIG = DeployConfig(
    PROJECT_ID="your-project-id",
    REPO="capstone-repo",
    DB_PASSWORD="replace-me",
    BUCKET_NAME="your-project-id-capstone-artifacts",
    OPENAI_API_KEY="sk-...",
)
```

## Worker Runtime (VM only)

Set `WORKER_RUNTIME` in `infra/gcp/deploy_config.py` to:
- `compute_engine`

Cloud Run worker mode is no longer supported in these deploy scripts.

Example:
```python
CONFIG = DeployConfig(
    PROJECT_ID="your-project-id",
    REPO="capstone-repo",
    DB_PASSWORD="replace-me",
    BUCKET_NAME="your-project-id-capstone-artifacts",
    WORKER_RUNTIME="compute_engine",
)
```

When using `compute_engine`, configure VM defaults as needed:
- `WORKER_VM_NAME`, `WORKER_VM_ZONE`, `WORKER_VM_MACHINE_TYPE`
- `WORKER_VM_DISK_SIZE_GB`
- `WORKER_VM_ENABLE_RDP`, `WORKER_VM_RDP_SOURCE`
- `WORKER_VM_USER`

Notes:
- Existing shell env vars still override Python file values.
- `OPENAI_API_KEY` resolution order is:
  1) shell env `OPENAI_API_KEY` (if already set)
  2) `infra/gcp/deploy_config.py` (`OPENAI_API_KEY`)
  3) `backend/config.py` (`OPENAI_API_KEY`)

## Shell Environment Variable Alternative

```bash
export PROJECT_ID="your-project-id"
export REGION="us-central1"
export REPO="capstone-repo"

export IMAGE_API="audio-summarizer-api"
# optional (frontend service)
export IMAGE_WEB="audio-summarizer-web"

export CLOUD_SQL_INSTANCE="capstone-sql"
export DB_NAME="capstone"
export DB_USER="capstone"
export DB_PASSWORD="replace-me"

export REDIS_INSTANCE="capstone-redis"
export BUCKET_NAME="${PROJECT_ID}-capstone-artifacts"
export OPENAI_API_KEY="sk-..."
```

Optional environment variables (if you want to override Python file/defaults):

```bash
export NETWORK="default"
export VPC_CONNECTOR="capstone-connector"
export VPC_CONNECTOR_RANGE="10.8.0.0/28"
export SQL_EDITION="ENTERPRISE"
export SQL_TIER="db-custom-2-7680"

export API_SERVICE="audio-summarizer-api"
export WORKER_SERVICE="audio-summarizer-worker"
export WEB_SERVICE="audio-summarizer-web"
export WEB_PORT="80"
export WORKER_RUNTIME="compute_engine"

export API_SERVICE_ACCOUNT="audio-summarizer-api-sa"
export WORKER_SERVICE_ACCOUNT="audio-summarizer-worker-sa"
export WORKER_VM_SERVICE_ACCOUNT="audio-summarizer-worker-vm-sa"
export WORKER_VM_NAME="audio-summarizer-worker-vm"
export WORKER_VM_ZONE="us-central1-a"
export WORKER_VM_MACHINE_TYPE="e2-standard-2"
export WORKER_VM_DISK_SIZE_GB="64"
export WORKER_VM_IMAGE_FAMILY="debian-12"
export WORKER_VM_IMAGE_PROJECT="debian-cloud"
export WORKER_VM_NETWORK="default"
export WORKER_VM_ENABLE_RDP="true"
export WORKER_VM_RDP_SOURCE="0.0.0.0/0"
export WORKER_VM_USER="worker"

export CORS_ALLOW_ORIGINS="*"
export WEBAPP_ENABLE_PGVECTOR="true"
export RQ_RETRY_MAX="3"
export RQ_RETRY_INTERVALS="30,120,300"
export RUN_VALIDATE_DEPLOY="false"
export YTDLP_STRICT_COOKIES="true"  # default: require cookie-auth for YouTube
export YTDLP_COOKIES_FROM_BROWSER="chrome"
export YTDLP_COOKIES_FROM_BROWSER_PROFILE="Default"
export YTDLP_COOKIES=""         # optional path in container
export YTDLP_COOKIES_FILE=""    # optional local cookies.txt path (VM workflow usually uses browser profile cookies instead)
export YTDLP_COOKIES_B64=""     # optional base64 cookies.txt contents
export YOUTUBE_TRANSCRIPT_API_FALLBACK="false"  # optional fallback path; disabled by default

# optional domain mapping
export API_DOMAIN="api.example.com"
export WEB_DOMAIN="app.example.com"
```

Cloud SQL tier/edition tip:
- If bootstrap fails with `Invalid Tier (...) for (ENTERPRISE_PLUS) Edition`, set in `infra/gcp/deploy_config.py`:
```python
SQL_EDITION="ENTERPRISE_PLUS"
SQL_TIER="db-perf-optimized-N-2"
```
Then rerun deploy.

## What each script does

### `bootstrap.sh`
- Enables required APIs
- Creates Artifact Registry repo
- Creates Cloud SQL instance/database/user
- Creates Redis instance
- Creates GCS bucket
- Creates Serverless VPC connector (Cloud Run -> Redis connectivity)
- Creates API/worker service accounts
- Grants least-privilege IAM roles:
  - `roles/cloudsql.client`
  - `roles/logging.logWriter`
  - `roles/monitoring.metricWriter`
  - `roles/storage.objectAdmin` (bucket-level on artifact bucket)

### `build_and_push.sh`
- Builds/pushes API image
- Optionally builds/pushes web image when `IMAGE_WEB` is set
- If `docker-credential-gcloud` is missing in Bash/WSL PATH, script auto-falls back to `gcloud auth print-access-token | docker login ...`

### `deploy_api.sh`
- Builds/pushes API image by default, then deploys API Cloud Run service
- Attaches Cloud SQL and VPC connector
- Sets Redis/GCS/OpenAI env vars
- Sets `API_BASE_URL` automatically to deployed service URL
- Skip image build/push only if needed:
```bash
BUILD_AND_PUSH_API=false bash infra/gcp/deploy_api.sh
```

### `provision_worker_vm.sh`
- Provisions/updates a persistent Compute Engine VM for worker runtime
- Installs:
  - Python + ffmpeg
  - Cloud SQL Auth Proxy
  - Google Chrome
  - XFCE + XRDP (for one-time browser login)
- Creates/updates systemd units:
  - `cloud-sql-proxy`
  - `capstone-worker`
- Creates a VM service account and grants least-privilege roles

### `deploy_worker_vm.sh`
- Syncs worker source code (`backend/` + `requirements.txt`) to VM
- Creates/updates virtualenv and installs requirements
- Writes `/etc/capstone/worker.env`
- Restarts `cloud-sql-proxy` and `capstone-worker`
- Also disables any legacy Cloud Run worker service by scaling to `min-instances=0` and moving it to a shadow queue (`RQ_QUEUE_NAME=cloudrun-disabled`)

### `provision_worker_vm.ps1` / `deploy_worker_vm.ps1`
- PowerShell wrappers for Windows that run the corresponding `.sh` scripts

### `deploy_web.sh`
- Builds web image with `VITE_API_BASE_URL` from deployed API service URL
- Deploys frontend Cloud Run service
- Deploys web service on container port `80` by default (`WEB_PORT`, override if needed)
- Includes the same Docker auth fallback when `docker-credential-gcloud` is unavailable

### Docker auth troubleshooting
- If you see:
```text
error getting credentials - err: exec: "docker-credential-gcloud": executable file not found in $PATH
```
- Cause: Docker is configured to use the gcloud credential helper, but that helper is not available in your Bash environment.
- Current scripts handle this automatically by removing the helper mapping for your Artifact Registry host and using token-based `docker login`.

### YouTube bot-check failures (VM worker)
- Symptom: jobs fail with errors like `Sign in to confirm you're not a bot`.
- Cause: YouTube often blocks cloud-provider IP ranges for transcript/download requests.
- Current behavior:
  - Worker uses cookie-authenticated yt-dlp extraction/downloading by default.
  - `YTDLP_STRICT_COOKIES=true` is default, so worker will not do cookieless shortcuts.
  - `YOUTUBE_TRANSCRIPT_API_FALLBACK=false` is default, so transcript-api fallback is also disabled.
  - In `prefer_youtube_captions` mode, worker tries automatic YouTube captions first; if captions are unavailable, it normally falls back to media processing + Whisper transcription.
  - If bot-block is detected and no cookie/proxy config is present, worker fails fast with a clear remediation message.
  - If you explicitly enable fallback env vars, non-cookie fallback paths can be used.
- Mitigations:
  - Provide cookies to worker via Chrome browser profile on the VM (recommended).
  - Add proxy egress for worker outbound traffic when YouTube keeps rate-limiting cloud IPs.
    - Set in `infra/gcp/deploy_config.py`:
      - `PROXY_ENABLED="true"`
      - `PROXY_CAPTIONS_ONLY="true"` (recommended: proxy captions path, keep Whisper/audio download direct)
      - `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` (or `PROXY_POOL`)
      - `PROXY_ROTATION_MODE="on_rate_limit"`
      - `PROXY_MAX_RETRIES="3"`
      - `PROXY_BACKOFF_SECONDS="2"`
      - `OPENAI_TRUST_ENV_PROXY="false"` (recommended so OpenAI Whisper/chat calls stay direct and only YouTube egress uses proxy pool logic)
      - Webshare rotating endpoint example:
        - `PROXY_POOL="http://<username>:<password>@p.webshare.io:80"`
        - or set `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` to that same endpoint
    - Optional auto-generation (instead of manually listing all proxies):
      - `PROXY_AUTOGENERATE="true"`
      - `PROXY_AUTOGENERATE_TEMPLATE="http://user:pass@proxy{i}.provider.net:80{i}"`
      - `PROXY_AUTOGENERATE_START="1"`
      - `PROXY_AUTOGENERATE_END="3"`
    - Then redeploy VM worker: `bash infra/gcp/deploy_worker_vm.sh`
  - Advanced fallback options:
    - `YTDLP_COOKIES_FILE` / `YTDLP_COOKIES_B64` / `YTDLP_COOKIES`
  - Or submit local/uploaded media files instead of YouTube URLs for blocked videos.

Quick fix (recommended):
1. RDP into the worker VM and sign into YouTube in Chrome once.
2. Redeploy worker only:
```bash
bash infra/gcp/deploy_worker_vm.sh
```
3. Re-submit the failed job URL.

### Desktop-like browser cookies (VM worker mode)
- If you want behavior closest to Tkinter desktop app, use:
  - `WORKER_RUNTIME="compute_engine"`
- Detailed walkthrough (password + RDP + Chrome sign-in):
  - `infra/gcp/WORKER_VM_CHROME_LOGIN.md`
- Then run:
```bash
bash infra/gcp/provision_worker_vm.sh
bash infra/gcp/deploy_worker_vm.sh
```
- After provisioning:
  1. Set Linux password for VM user:
  ```bash
  gcloud compute ssh audio-summarizer-worker-vm --zone us-central1-a --command "sudo passwd worker"
  ```
  2. RDP into VM (`<external-ip>:3389`) as `worker`.
  3. Open Chrome on VM and sign into YouTube once.
  4. Keep:
  - `YTDLP_STRICT_COOKIES=true`
  - `YTDLP_COOKIES_FROM_BROWSER=chrome`
  - `YTDLP_COOKIES_FROM_BROWSER_PROFILE=Default` (or your VM profile)
  5. Re-run `bash infra/gcp/deploy_worker_vm.sh` to refresh env/service.

### `map_domain.sh`
- Creates Cloud Run domain mapping for any service/domain pair
- Managed TLS certificates are handled by Cloud Run after DNS is set

### `deploy_all.sh`
- Orchestrates bootstrap -> build/push -> deploy api -> provision worker VM -> deploy worker VM -> optional web deploy -> optional domain mapping
- Requires `WORKER_RUNTIME=compute_engine`

### `deploy_all.ps1`
- PowerShell wrapper for Windows that runs `deploy_all.sh`
- Checks `bash`, `gcloud`, and `docker` are in PATH first

### `pause_stack.sh`
- Cost-saving pause script (idempotent; safe to rerun)
- Uses values from `infra/gcp/deploy_config.py` (or env overrides)
- Actions:
  - sets legacy Cloud Run worker `min-instances=0` (if service exists)
  - stops worker VM
  - sets Cloud SQL activation policy to `NEVER` (instance powers down)
- Run:
```bash
bash infra/gcp/pause_stack.sh
```

### `resume_stack.sh`
- Bring services back online after pause (idempotent; safe to rerun)
- Uses values from `infra/gcp/deploy_config.py` (or env overrides)
- Actions:
  - sets Cloud SQL activation policy to `ALWAYS` and waits for `RUNNABLE`
  - starts worker VM and checks worker services
- Run:
```bash
bash infra/gcp/resume_stack.sh
```

Notes:
- These scripts do not delete resources; they only pause/resume runtime components.
- Memorystore Redis is not paused by these commands (Google keeps it running until deleted).

### `validate_deploy.sh`
- Verifies:
  - API `/healthz`
  - web URL (if deployed)
  - worker ingress is `internal`
  - worker IAM is not public
- Optional smoke checks:
  - submit and wait for a real YouTube job (`SMOKE_YOUTUBE_URL`)
  - run global search (`SEARCH_QUESTION`)

### `validate_deploy.ps1`
- PowerShell wrapper for Windows that runs `validate_deploy.sh`

### `run_backfill_with_cloudsql_proxy.sh`
- Starts `cloud-sql-proxy` to Cloud SQL from local machine
- Sets `DATABASE_URL` + `OBJECT_STORAGE_BACKEND=gcs`
- Runs `python -m backend.webapp.migrate`
- Runs `python -m backend.webapp.backfill_from_storage --storage-root ...`

### `run_backfill_with_cloudsql_proxy.ps1`
- PowerShell wrapper for Windows that runs `run_backfill_with_cloudsql_proxy.sh`

## Typical sequence

```bash
# one-shot workflow
bash infra/gcp/deploy_all.sh

# one-shot + automatic validation
RUN_VALIDATE_DEPLOY=true bash infra/gcp/deploy_all.sh
```

Windows PowerShell:

```powershell
.\infra\gcp\deploy_all.ps1
```

Or step-by-step:

```bash
bash infra/gcp/bootstrap.sh
bash infra/gcp/build_and_push.sh   # API + optional web
bash infra/gcp/deploy_api.sh
bash infra/gcp/provision_worker_vm.sh
bash infra/gcp/deploy_worker_vm.sh
# optional frontend deploy
bash infra/gcp/deploy_web.sh
```

If you already built images and only want to redeploy services:

```bash
BUILD_AND_PUSH_API=false bash infra/gcp/deploy_api.sh
bash infra/gcp/deploy_worker_vm.sh
# optional frontend redeploy
bash infra/gcp/deploy_web.sh
```

## What Is Still Manual (By Design)

- YouTube Chrome login on VM:
  - First-time required.
  - Occasionally required again if cookies/session expire or YouTube re-challenges.
- Local auth/tools on your PC:
  - Keep `gcloud` authenticated:
    - scripts execute `gcloud` from your machine to create/update cloud resources.
  - Keep Docker Desktop healthy/running before deploy:
    - scripts do local `docker build` from repo Dockerfiles and `docker push` to Artifact Registry.
    - deployed services then pull images from Artifact Registry.
    - GCP is not reading local Dockerfiles directly in this script path.
    - On Windows, Docker CLI calls into Docker Desktop's local daemon (`dockerDesktopLinuxEngine`) to do the actual build/push work.
    - If Docker Desktop is closed/unhealthy, that daemon is unavailable and deploy fails before new images are published.
    - Common error: `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`.
- Optional custom domain DNS:
  - Only required if you set `API_DOMAIN` / `WEB_DOMAIN`.
- Optional historical backfill:
  - Only required if you want old local jobs in `storage/` to appear online.
  - Command: `python -m backend.webapp.backfill_from_storage`

## Fast env setup

Use the template:

```bash
cp infra/gcp/env.example.sh infra/gcp/env.sh
# edit infra/gcp/env.sh
source infra/gcp/env.sh
```

## Post-deploy validation

```bash
# Basic checks
bash infra/gcp/validate_deploy.sh

# Full smoke checks (optional, costs API usage)
SMOKE_YOUTUBE_URL="https://www.youtube.com/watch?v=..." \
SEARCH_QUESTION="What are the key takeaways?" \
bash infra/gcp/validate_deploy.sh
```

Windows PowerShell:

```powershell
.\infra\gcp\validate_deploy.ps1
```

## Database migration and backfill

> Note:
> For this public Cloud Run setup, excluding local artifacts from Docker builds is the right default.
> Do not bake `storage/` job artifacts into container images.
> If you want old local jobs available online, backfill them instead:
> `python -m backend.webapp.backfill_from_storage`

After deploy, run from a trusted environment with DB connectivity:

```bash
python -m backend.webapp.migrate
python -m backend.webapp.backfill_from_storage
```

If `OPENAI_API_KEY` is set during backfill, vector embeddings/chunks are re-indexed.

If you are running from your local machine and need Cloud SQL connectivity:

```bash
LOCAL_STORAGE_ROOT="storage" bash infra/gcp/run_backfill_with_cloudsql_proxy.sh
```

Windows PowerShell:

```powershell
.\infra\gcp\run_backfill_with_cloudsql_proxy.ps1
```

## Domain mapping

To map custom domains:

```bash
SERVICE="${API_SERVICE}" DOMAIN="${API_DOMAIN}" REGION="${REGION}" bash infra/gcp/map_domain.sh
SERVICE="${WEB_SERVICE}" DOMAIN="${WEB_DOMAIN}" REGION="${REGION}" bash infra/gcp/map_domain.sh
```

Then apply DNS records shown by:

```bash
gcloud beta run domain-mappings describe --domain "${API_DOMAIN}" --region "${REGION}"
```

## Security notes

- Keep `OPENAI_API_KEY` server-side only.
- For stronger key hygiene, migrate scripts to use Secret Manager references instead of plain env var injection.
- Worker is deployed with internal ingress and no unauthenticated access by default.
