"""
Single-source deploy configuration for infra/gcp scripts.

Set values here once, and the *.sh scripts will auto-load them when shell env
vars are not already set.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DeployConfig:
    # Required for most deploy scripts
    PROJECT_ID: Optional[str] = None
    REGION: str = "us-central1"
    REPO: Optional[str] = None
    IMAGE_API: str = "audio-summarizer-api"
    IMAGE_WEB: Optional[str] = "audio-summarizer-web"

    CLOUD_SQL_INSTANCE: Optional[str] = "capstone-sql"
    SQL_EDITION: str = "ENTERPRISE"
    SQL_TIER: str = "db-custom-2-7680"
    DB_NAME: Optional[str] = "capstone"
    DB_USER: Optional[str] = "capstone"
    DB_PASSWORD: Optional[str] = None

    REDIS_INSTANCE: Optional[str] = "capstone-redis"
    BUCKET_NAME: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None

    # Optional with defaults
    NETWORK: str = "default"
    VPC_CONNECTOR: str = "capstone-connector"
    VPC_CONNECTOR_RANGE: str = "10.8.0.0/28"

    API_SERVICE: str = "audio-summarizer-api"
    WORKER_SERVICE: str = "audio-summarizer-worker"
    WEB_SERVICE: str = "audio-summarizer-web"
    WORKER_RUNTIME: str = "compute_engine"  # VM worker is the supported mode

    API_SERVICE_ACCOUNT: str = "audio-summarizer-api-sa"
    WORKER_SERVICE_ACCOUNT: str = "audio-summarizer-worker-sa"
    WORKER_VM_SERVICE_ACCOUNT: str = "audio-summarizer-worker-vm-sa"

    # Compute Engine worker settings (used when WORKER_RUNTIME="compute_engine")
    WORKER_VM_NAME: str = "audio-summarizer-worker-vm"
    WORKER_VM_ZONE: str = "us-central1-a"
    WORKER_VM_MACHINE_TYPE: str = "e2-standard-2"
    WORKER_VM_DISK_SIZE_GB: str = "64"
    WORKER_VM_IMAGE_FAMILY: str = "debian-12"
    WORKER_VM_IMAGE_PROJECT: str = "debian-cloud"
    WORKER_VM_NETWORK: str = "default"
    WORKER_VM_SUBNET: Optional[str] = None
    WORKER_VM_ENABLE_RDP: str = "true"
    WORKER_VM_RDP_SOURCE: str = "0.0.0.0/0"
    WORKER_VM_USER: str = "worker"

    CORS_ALLOW_ORIGINS: str = "*"
    WEBAPP_ENABLE_PGVECTOR: str = "true"
    RQ_RETRY_MAX: str = "3"
    RQ_RETRY_INTERVALS: str = "30,120,300"
    RUN_VALIDATE_DEPLOY: str = "false"

    # Optional YouTube auth/session cookies
    YTDLP_STRICT_COOKIES: str = "true"
    YTDLP_COOKIES_FROM_BROWSER: str = "chrome"
    YTDLP_COOKIES_FROM_BROWSER_PROFILE: str = "Default"
    YTDLP_COOKIES: Optional[str] = None
    YTDLP_COOKIES_FILE: Optional[str] = None
    YTDLP_COOKIES_TEXT: Optional[str] = None
    YTDLP_COOKIES_B64: Optional[str] = None
    YTDLP_JS_RUNTIMES: str = "node"
    YTDLP_REMOTE_COMPONENTS: Optional[str] = None
    YOUTUBE_TRANSCRIPT_API_FALLBACK: str = "false"

    # Optional domain mapping
    API_DOMAIN: Optional[str] = None
    WEB_DOMAIN: Optional[str] = None

    # Optional validation inputs
    SMOKE_TIMEOUT_SECONDS: str = "1200"
    SMOKE_POLL_SECONDS: str = "10"
    SMOKE_YOUTUBE_URL: Optional[str] = None
    SEARCH_QUESTION: Optional[str] = None

    # Optional backfill controls
    LOCAL_STORAGE_ROOT: str = "storage"
    CLOUD_SQL_PROXY_PORT: str = "5432"
    BACKFILL_ENABLE_EMBEDDINGS: str = "true"


#CONFIG = DeployConfig()
CONFIG = DeployConfig(
    PROJECT_ID="tribal-primer-438802-n0",
    REPO="capstone-repo",
    DB_PASSWORD="1e344de5940d4378ac21fc80ae03dccb",
    BUCKET_NAME="tribal-primer-438802-n0-capstone-artifacts",
    WORKER_RUNTIME="compute_engine",
    SQL_TIER="db-custom-1-3840",
    WORKER_VM_MACHINE_TYPE="e2-medium",
    OPENAI_API_KEY=None,  # uses backend/config.py fallback
    YTDLP_COOKIES_FILE=None,
)

def _fallback_openai_key() -> Optional[str]:
    """
    Reuse repository-wide OpenAI key fallback when deploy config leaves it unset.
    """
    repo_root = Path(__file__).resolve().parents[2]
    try:
        import sys

        sys.path.insert(0, str(repo_root))
        from backend.config import get_openai_api_key

        return get_openai_api_key()
    except Exception:
        return None


def get_deploy_env() -> dict[str, str]:
    values = asdict(CONFIG)
    if not values.get("OPENAI_API_KEY"):
        values["OPENAI_API_KEY"] = _fallback_openai_key()

    out: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        out[key] = text
    return out
