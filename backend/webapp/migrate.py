from __future__ import annotations

import logging
import os

from sqlalchemy import text

from .db import engine, init_db
from .config import settings


logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _ensure_allow_whisper_fallback_column(conn, dialect: str) -> None:
    """Add jobs.allow_whisper_fallback for older deployments."""
    try:
        if dialect == "postgresql":
            conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS allow_whisper_fallback BOOLEAN DEFAULT TRUE"))
            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET allow_whisper_fallback = TRUE
                    WHERE allow_whisper_fallback IS NULL
                    """
                )
            )
            return

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(jobs)")).mappings().all()
            column_names = {str(row.get("name")) for row in rows}
            if "allow_whisper_fallback" not in column_names:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN allow_whisper_fallback BOOLEAN DEFAULT 1"))
            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET allow_whisper_fallback = 1
                    WHERE allow_whisper_fallback IS NULL
                    """
                )
            )
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to migrate jobs.allow_whisper_fallback column: %s", exc)


def _ensure_owner_email_column(conn, dialect: str) -> None:
    """Add jobs.owner_email and backfill legacy rows to configured owner email."""
    owner_email = (settings.legacy_jobs_owner_email or "").strip().lower() or "danmcneary8@gmail.com"
    try:
        if dialect == "postgresql":
            conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS owner_email VARCHAR(320)"))
            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET owner_email = :owner_email
                    WHERE owner_email IS NULL OR owner_email = ''
                    """
                ),
                {"owner_email": owner_email},
            )
            conn.execute(text("ALTER TABLE jobs ALTER COLUMN owner_email SET NOT NULL"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_jobs_owner_email ON jobs(owner_email)"))
            return

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(jobs)")).mappings().all()
            column_names = {str(row.get("name")) for row in rows}
            if "owner_email" not in column_names:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN owner_email TEXT"))
            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET owner_email = :owner_email
                    WHERE owner_email IS NULL OR owner_email = ''
                    """
                ),
                {"owner_email": owner_email},
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_jobs_owner_email ON jobs(owner_email)"))
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to migrate jobs.owner_email column: %s", exc)


def _ensure_completed_at_column(conn, dialect: str) -> None:
    """Add jobs.completed_at and backfill historical complete rows."""
    try:
        if dialect == "postgresql":
            conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ"))
            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET completed_at = updated_at
                    WHERE status = 'complete'
                      AND completed_at IS NULL
                    """
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_jobs_completed_at ON jobs(completed_at)"))
            return

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(jobs)")).mappings().all()
            column_names = {str(row.get("name")) for row in rows}
            if "completed_at" not in column_names:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN completed_at TIMESTAMP"))
            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET completed_at = updated_at
                    WHERE status = 'complete'
                      AND completed_at IS NULL
                    """
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_jobs_completed_at ON jobs(completed_at)"))
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to migrate jobs.completed_at column: %s", exc)


def _ensure_digest_backfill_column(conn, dialect: str) -> None:
    """Add digest_preferences.include_historical_on_next_send and backfill legacy rows."""
    try:
        if dialect == "postgresql":
            conn.execute(
                text(
                    """
                    ALTER TABLE digest_preferences
                    ADD COLUMN IF NOT EXISTS include_historical_on_next_send BOOLEAN DEFAULT FALSE
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE digest_preferences
                    SET include_historical_on_next_send = FALSE
                    WHERE include_historical_on_next_send IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE digest_preferences
                    SET include_historical_on_next_send = TRUE
                    WHERE last_sent_at IS NULL
                    """
                )
            )
            return

        if dialect == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(digest_preferences)")).mappings().all()
            column_names = {str(row.get("name")) for row in rows}
            if "include_historical_on_next_send" not in column_names:
                conn.execute(
                    text(
                        """
                        ALTER TABLE digest_preferences
                        ADD COLUMN include_historical_on_next_send BOOLEAN DEFAULT 0
                        """
                    )
                )
            conn.execute(
                text(
                    """
                    UPDATE digest_preferences
                    SET include_historical_on_next_send = 0
                    WHERE include_historical_on_next_send IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE digest_preferences
                    SET include_historical_on_next_send = 1
                    WHERE last_sent_at IS NULL
                    """
                )
            )
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to migrate digest_preferences.include_historical_on_next_send column: %s", exc)


def run_migrations() -> None:
    """
    Minimal migration entrypoint:
    - creates SQLAlchemy tables
    - optionally enables pgvector in Postgres and adds `embedding_vector`
    """
    init_db()
    with engine.begin() as conn:
        dialect = conn.dialect.name
        logger.info("Running migrations for dialect=%s", dialect)

        _ensure_allow_whisper_fallback_column(conn, dialect)
        _ensure_owner_email_column(conn, dialect)
        _ensure_completed_at_column(conn, dialect)
        _ensure_digest_backfill_column(conn, dialect)

    with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect != "postgresql":
            return

        if not _env_bool("WEBAPP_ENABLE_PGVECTOR", True):
            logger.info("WEBAPP_ENABLE_PGVECTOR is disabled; skipping pgvector extension steps.")
            return

        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("ALTER TABLE vectors ADD COLUMN IF NOT EXISTS embedding_vector vector(3072)"))
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_vectors_embedding_cosine
                    ON vectors USING ivfflat (embedding_vector vector_cosine_ops)
                    WITH (lists = 100)
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE vectors
                    SET embedding_vector = embedding_json::vector
                    WHERE embedding_json IS NOT NULL
                      AND embedding_json <> ''
                      AND embedding_vector IS NULL
                    """
                )
            )
            logger.info("pgvector extension and vector index migration completed.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("pgvector migration step failed: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migrations()
