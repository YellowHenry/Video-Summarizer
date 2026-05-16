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
    SQL_STORAGE_TYPE: Optional[str] = None
    DB_NAME: Optional[str] = "capstone"
    DB_USER: Optional[str] = "capstone"
    DB_PASSWORD: Optional[str] = None

    REDIS_RUNTIME: str = "memorystore"  # memorystore|worker_vm
    REDIS_INSTANCE: Optional[str] = "capstone-redis"
    REDIS_VM_PORT: str = "6379"
    REDIS_VM_REQUIREPASS: Optional[str] = None
    REDIS_VM_FIREWALL_RULE: str = "capstone-worker-redis-allow"
    REDIS_VM_ALLOWED_SOURCE: Optional[str] = None
    BUCKET_NAME: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_SECRET_NAME: Optional[str] = None
    GOOGLE_OAUTH_CLIENT_ID: Optional[str] = None
    OPENAI_TRUST_ENV_PROXY: str = "false"
    SUMMARIZER_MAX_TOKENS: str = "800"
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: str = "587"
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: Optional[str] = None
    WEB_APP_BASE_URL: Optional[str] = None
    DIGEST_SWEEP_SECRET: Optional[str] = None
    DIGEST_SWEEP_INTERVAL_MINUTES: str = "15"
    DIGEST_PROFILE_MAX_JOBS: str = "20"
    DIGEST_MAX_ITEMS_PER_EMAIL: str = "10"
    DIGEST_JOB_EXCERPT_CHARS: str = "240"
    DIGEST_SEND_HOUR_LOCAL: str = "8"
    DIGEST_WEEKLY_WEEKDAY: str = "0"
    DIGEST_SWEEP_JOB_NAME: str = "audio-summarizer-digest-sweep"

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
    # Proxy egress controls (VM worker)
    PROXY_ENABLED: str = "false"
    PROXY_CAPTIONS_ONLY: str = "false"
    PROXY_ROTATION_MODE: str = "on_rate_limit"  # none|per_job|on_rate_limit
    PROXY_MAX_RETRIES: str = "3"
    PROXY_BACKOFF_SECONDS: str = "2"
    PROXY_POOL: Optional[str] = None
    # Optional proxy auto-generation from one template, e.g.:
    # "http://user:pass@proxy{i}.provider.net:1000{i}"
    PROXY_AUTOGENERATE: str = "false"
    PROXY_AUTOGENERATE_TEMPLATE: Optional[str] = None
    PROXY_AUTOGENERATE_START: str = "1"
    PROXY_AUTOGENERATE_END: str = "1"

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


# Keep this file publish-safe. Put real project IDs, passwords, proxy URLs,
# OAuth client IDs, SMTP credentials, and digest secrets in ignored
# infra/gcp/env.sh or shell environment variables.
CONFIG = DeployConfig(
    REPO="capstone-repo",
    SQL_STORAGE_TYPE="HDD",
    WORKER_RUNTIME="compute_engine",
    SQL_TIER="db-f1-micro",
    WORKER_VM_MACHINE_TYPE="e2-small",
    WORKER_VM_DISK_SIZE_GB="32",
    OPENAI_SECRET_NAME="openai-api-key",
    SUMMARIZER_MAX_TOKENS="1500",
    REDIS_RUNTIME="worker_vm",
    PROXY_ENABLED="false",
    PROXY_CAPTIONS_ONLY="true",
    PROXY_ROTATION_MODE="on_rate_limit",
    PROXY_MAX_RETRIES="3",
    PROXY_BACKOFF_SECONDS="2",
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


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(raw: str, default: int) -> int:
    try:
        return int(raw.strip())
    except Exception:
        return default


def _parse_csv(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _expand_proxy_template(template: str, start: int, end: int) -> list[str]:
    template = template.strip()
    if not template:
        return []
    if "{i" not in template and "{index" not in template and "{n" not in template:
        return [template]
    step = 1 if end >= start else -1
    out: list[str] = []
    for i in range(start, end + step, step):
        try:
            value = template.format(i=i, index=i, n=i).strip()
        except Exception:
            continue
        if value:
            out.append(value)
    return out


def _apply_proxy_env(values: dict[str, Optional[str]]) -> None:
    proxy_enabled = _truthy(str(values.get("PROXY_ENABLED") or "false"))
    if not proxy_enabled:
        return

    pool = _parse_csv(values.get("PROXY_POOL"))

    if _truthy(str(values.get("PROXY_AUTOGENERATE") or "false")):
        template = str(values.get("PROXY_AUTOGENERATE_TEMPLATE") or "")
        start = _safe_int(str(values.get("PROXY_AUTOGENERATE_START") or "1"), 1)
        end = _safe_int(str(values.get("PROXY_AUTOGENERATE_END") or str(start)), start)
        pool.extend(_expand_proxy_template(template, start, end))

    pool = _dedupe(pool)
    if pool:
        values["PROXY_POOL"] = ",".join(pool)


def get_deploy_env() -> dict[str, str]:
    values = asdict(CONFIG)
    if not values.get("OPENAI_API_KEY") and not values.get("OPENAI_SECRET_NAME"):
        values["OPENAI_API_KEY"] = _fallback_openai_key()
    _apply_proxy_env(values)

    out: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        out[key] = text
    return out
