import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_sqlite_url() -> str:
    db_path = Path("storage") / "webapp.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path.as_posix()}"


def _env_int_tuple(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values: list[int] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        try:
            values.append(int(token))
        except ValueError:
            continue
    return tuple(values) if values else default


@dataclass
class WebSettings:
    database_url: str = os.getenv("DATABASE_URL") or _default_sqlite_url()
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_name: str = os.getenv("RQ_QUEUE_NAME", "jobs")
    sync_jobs: bool = _env_bool("WEBAPP_SYNC_JOBS", False)
    rq_retry_max: int = int(os.getenv("RQ_RETRY_MAX", "3"))
    rq_retry_intervals: tuple[int, ...] = _env_int_tuple("RQ_RETRY_INTERVALS", (30, 120, 300))

    object_backend: str = os.getenv("OBJECT_STORAGE_BACKEND", "").strip().lower() or (
        "gcs" if os.getenv("GCS_BUCKET") else "local"
    )
    gcs_bucket: str = os.getenv("GCS_BUCKET", "")
    local_object_root: Path = Path(os.getenv("LOCAL_OBJECT_ROOT", "storage/objects"))

    cors_allow_origins: str = os.getenv("CORS_ALLOW_ORIGINS", "*")
    api_base_url: str = os.getenv("API_BASE_URL", "http://localhost:8000")
    upload_url_expiry_seconds: int = int(os.getenv("UPLOAD_URL_EXPIRY_SECONDS", "900"))
    download_url_expiry_seconds: int = int(os.getenv("DOWNLOAD_URL_EXPIRY_SECONDS", "900"))
    webapp_google_client_id: str = os.getenv("WEBAPP_GOOGLE_CLIENT_ID", "").strip()
    webapp_disable_auth: bool = _env_bool("WEBAPP_DISABLE_AUTH", False)
    webapp_dev_user_email: str = os.getenv("WEBAPP_DEV_USER_EMAIL", "dev@example.com")
    legacy_jobs_owner_email: str = os.getenv("LEGACY_JOBS_OWNER_EMAIL", "danmcneary8@gmail.com")


settings = WebSettings()
