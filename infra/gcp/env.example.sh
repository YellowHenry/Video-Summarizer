#!/usr/bin/env bash
# Copy this to env.sh and fill values, then:
#   source infra/gcp/env.sh

export PROJECT_ID="your-project-id"
export REGION="us-central1"
export REPO="capstone-repo"

export IMAGE_API="audio-summarizer-api"
export IMAGE_WEB="audio-summarizer-web"

export CLOUD_SQL_INSTANCE="capstone-sql"
export SQL_TIER="db-f1-micro"          # cost-optimized default target for low traffic
# export SQL_STORAGE_TYPE="HDD"         # optional extra savings (if acceptable / supported)
export DB_NAME="capstone"
export DB_USER="capstone"
export DB_PASSWORD="replace-me"

export REDIS_RUNTIME="worker_vm"       # memorystore|worker_vm
export REDIS_INSTANCE="capstone-redis"
export REDIS_VM_PORT="6379"
# Set this for worker_vm mode; prefer env/local file, do not commit secrets
# export REDIS_VM_REQUIREPASS="replace-me-with-random-password"
export REDIS_VM_FIREWALL_RULE="capstone-worker-redis-allow"
# If unset, scripts default to VPC_CONNECTOR_RANGE for Cloud Run -> VM Redis access
# export REDIS_VM_ALLOWED_SOURCE="10.8.0.0/28"
export BUCKET_NAME="${PROJECT_ID}-capstone-artifacts"
# Prefer Secret Manager for deployed OpenAI keys. Use OPENAI_API_KEY only for
# temporary local tests and never commit the real value.
export OPENAI_SECRET_NAME="openai-api-key"
# export OPENAI_API_KEY="replace-with-openai-key"
export GOOGLE_OAUTH_CLIENT_ID="your-google-oauth-client-id.apps.googleusercontent.com"
export WEB_APP_BASE_URL="https://your-web-app-url"
export DIGEST_SWEEP_INTERVAL_MINUTES="15"
export DIGEST_PROFILE_MAX_JOBS="20"
export DIGEST_MAX_ITEMS_PER_EMAIL="10"
export DIGEST_JOB_EXCERPT_CHARS="240"
export DIGEST_SEND_HOUR_LOCAL="8"
export DIGEST_WEEKLY_WEEKDAY="0"
# export DIGEST_SWEEP_SECRET="replace-me"
# export DIGEST_SWEEP_JOB_NAME="audio-summarizer-digest-sweep"
# SMTP required only if you enable email digests.
# export SMTP_HOST="smtp.sendgrid.net"
# export SMTP_PORT="587"
# export SMTP_USER="apikey"
# export SMTP_PASSWORD="replace-me"
# export SMTP_FROM="summaries@example.com"
# Keep OpenAI transport direct by default even when proxy vars are set for YouTube egress.
export OPENAI_TRUST_ENV_PROXY="false"

# Optional defaults
export API_SERVICE="audio-summarizer-api"
export WORKER_SERVICE="audio-summarizer-worker"
export WEB_SERVICE="audio-summarizer-web"
export WORKER_RUNTIME="compute_engine"  # VM worker is the supported mode
export API_SERVICE_ACCOUNT="audio-summarizer-api-sa"
export WORKER_SERVICE_ACCOUNT="audio-summarizer-worker-sa"
export VPC_CONNECTOR="capstone-connector"
export NETWORK="default"
export VPC_CONNECTOR_RANGE="10.8.0.0/28"
export CORS_ALLOW_ORIGINS="*"
export WEBAPP_ENABLE_PGVECTOR="true"
export RQ_RETRY_MAX="3"
export RQ_RETRY_INTERVALS="30,120,300"
export RUN_VALIDATE_DEPLOY="false"
export YTDLP_STRICT_COOKIES="true"
export YOUTUBE_TRANSCRIPT_API_FALLBACK="false"
export YTDLP_COOKIES_FROM_BROWSER="chrome"
export YTDLP_COOKIES_FROM_BROWSER_PROFILE="Default"
export YTDLP_JS_RUNTIMES="node"
export YTDLP_REMOTE_COMPONENTS=""

# Optional proxy egress (recommended if YouTube rate-limits your VM IP).
export PROXY_ENABLED="false"
# If true, proxies are only used for YouTube caption requests (not media/audio download).
export PROXY_CAPTIONS_ONLY="false"
# Proxy endpoint(s). If PROXY_ENABLED=true, set PROXY_POOL.
# For one endpoint, set a single URL. For multiple endpoints, use comma-separated URLs.
# export PROXY_POOL="http://u:p@proxy1:8080"
# Rotation + retries
export PROXY_ROTATION_MODE="on_rate_limit"  # none|per_job|on_rate_limit
export PROXY_MAX_RETRIES="3"
export PROXY_BACKOFF_SECONDS="2"
# Optional explicit pool (comma-separated) for rotation.
# export PROXY_POOL="http://u:p@proxy1:8080,http://u:p@proxy2:8080,http://u:p@proxy3:8080"
# Optional auto-generation: expands template for {i} in [START..END]
export PROXY_AUTOGENERATE="false"
# export PROXY_AUTOGENERATE_TEMPLATE="http://user:pass@proxy{i}.example.net:80{i}"
export PROXY_AUTOGENERATE_START="1"
export PROXY_AUTOGENERATE_END="3"

# VM worker settings (used when WORKER_RUNTIME=compute_engine)
export WORKER_VM_NAME="audio-summarizer-worker-vm"
export WORKER_VM_ZONE="us-central1-a"
export WORKER_VM_MACHINE_TYPE="e2-small"
export WORKER_VM_DISK_SIZE_GB="32"
export WORKER_VM_IMAGE_FAMILY="debian-12"
export WORKER_VM_IMAGE_PROJECT="debian-cloud"
export WORKER_VM_SERVICE_ACCOUNT="audio-summarizer-worker-vm-sa"
export WORKER_VM_NETWORK="default"
# export WORKER_VM_SUBNET="default"
export WORKER_VM_ENABLE_RDP="true"
export WORKER_VM_RDP_SOURCE="0.0.0.0/0"
export WORKER_VM_USER="worker"

# Optional domain mapping
# export API_DOMAIN="api.example.com"
# export WEB_DOMAIN="app.example.com"

# Optional deploy validation settings
# export SMOKE_YOUTUBE_URL="https://www.youtube.com/watch?v=..."
# export SEARCH_QUESTION="What were the main topics?"

# Optional backfill controls
# export LOCAL_STORAGE_ROOT="storage"
# export BACKFILL_ENABLE_EMBEDDINGS="true"
