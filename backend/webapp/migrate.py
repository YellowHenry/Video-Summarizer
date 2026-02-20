from __future__ import annotations

import logging
import os

from sqlalchemy import text

from .db import engine, init_db


logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
