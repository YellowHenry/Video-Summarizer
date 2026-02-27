import json
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path

from backend.vector_store import VectorStore

from .config import settings
from .db import SessionLocal
from .metadata_store import MetadataStore
from .migrate import run_migrations
from .models import JobRecord
from .object_store import get_object_store


logger = logging.getLogger(__name__)
PLACEHOLDER_PREFIX = "it seems that the transcript you intended to provide is missing"


def _meta_bool(meta: dict, key: str, default: bool) -> bool:
    value = meta.get(key, default)
    if value is None:
        return default
    return bool(value)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def run(storage_root: Path = Path("storage")) -> None:
    run_migrations()
    object_store = get_object_store()
    vector_store = VectorStore()
    can_index = bool(vector_store.client)
    if not can_index:
        logger.info("OpenAI key not configured; vector indexing is skipped during backfill.")
    db = SessionLocal()
    metadata = MetadataStore(db)
    imported = 0
    try:
        for job_dir in storage_root.iterdir() if storage_root.exists() else []:
            if not job_dir.is_dir() or job_dir.name == "index":
                continue
            job_id = job_dir.name
            meta_path = job_dir / "meta.json"
            summary_path = job_dir / "summary.txt"
            transcript_path = job_dir / "transcript.txt"
            title_path = job_dir / "title.txt"
            chat_path = job_dir / "chat.json"

            meta = _read_json(meta_path) if meta_path.exists() else {}
            existing = db.get(JobRecord, job_id)
            if existing:
                job = existing
            else:
                source_type = "youtube" if meta.get("youtube_url") else "upload"
                job = JobRecord(
                    id=job_id,
                    owner_email=(settings.legacy_jobs_owner_email or "danmcneary8@gmail.com").strip().lower(),
                    source_type=source_type,
                    source_url=meta.get("youtube_url"),
                    prefer_youtube_captions=_meta_bool(meta, "prefer_youtube_captions", True),
                    allow_whisper_fallback=_meta_bool(meta, "allow_whisper_fallback", True),
                )
                db.add(job)
                db.flush()
            if not job.owner_email:
                job.owner_email = (settings.legacy_jobs_owner_email or "danmcneary8@gmail.com").strip().lower()

            created_at_raw = meta.get("created_at")
            if created_at_raw:
                try:
                    created_at = datetime.fromtimestamp(float(created_at_raw), tz=timezone.utc)
                    job.created_at = created_at
                    job.updated_at = created_at
                except Exception:
                    pass

            job.status = meta.get("status", job.status or "complete")
            job.title = _read_text(title_path) or meta.get("title") or job.title
            job.allow_whisper_fallback = _meta_bool(
                meta,
                "allow_whisper_fallback",
                getattr(job, "allow_whisper_fallback", True),
            )
            job.transcript_source = meta.get("transcript_source")
            job.captions_attempted = meta.get("captions_attempted")
            job.captions_status = meta.get("captions_status")
            job.captions_detail = meta.get("captions_detail")

            summary_text = _read_text(summary_path)
            is_placeholder = bool(summary_text and summary_text.strip().lower().startswith(PLACEHOLDER_PREFIX))
            if is_placeholder:
                summary_text = None
                transcript_text = None
                job.status = "failed"
                job.error = "Placeholder summary detected during backfill; artifacts skipped."
                job.summary_object_key = None
                job.transcript_object_key = None
            else:
                transcript_text = _read_text(transcript_path)

            if summary_text:
                summary_key = f"jobs/{job_id}/summary.txt"
                object_store.put_text(summary_key, summary_text)
                job.summary_object_key = summary_key
                metadata.upsert_artifact(
                    job_id,
                    "summary",
                    summary_key,
                    size_bytes=len(summary_text.encode("utf-8")),
                    content_type="text/plain; charset=utf-8",
                )
            if transcript_text:
                transcript_key = f"jobs/{job_id}/transcript.txt"
                object_store.put_text(transcript_key, transcript_text)
                job.transcript_object_key = transcript_key
                metadata.upsert_artifact(
                    job_id,
                    "transcript",
                    transcript_key,
                    size_bytes=len(transcript_text.encode("utf-8")),
                    content_type="text/plain; charset=utf-8",
                )

            if chat_path.exists():
                try:
                    chat_items = json.loads(chat_path.read_text(encoding="utf-8"))
                except Exception:
                    chat_items = []
                metadata.replace_chat(job_id, chat_items)

            if can_index:
                try:
                    vector_store.remove_job_records(job_id)
                    if summary_text:
                        vector_store.add_text(
                            job_id,
                            summary_text,
                            job.source_url,
                            kind="summary",
                            file_path=summary_path,
                        )
                    if transcript_text:
                        vector_store.add_text(
                            job_id,
                            transcript_text,
                            job.source_url,
                            kind="transcript",
                            file_path=transcript_path,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to (re)index vectors for job %s: %s", job_id, exc)
                try:
                    db_records = [record for record in vector_store.records if record.job_id == job_id]
                    metadata.replace_vectors(job_id, db_records)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to sync vectors table for job %s: %s", job_id, exc)
            else:
                metadata.replace_vectors(job_id, [])

            imported += 1
        db.commit()
    finally:
        db.close()
    logger.info("Backfill complete. Imported/updated %d jobs.", imported)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill desktop storage into web metadata/object stores.")
    parser.add_argument(
        "--storage-root",
        default="storage",
        help="Root directory that contains storage/<job_id>/ folders (default: storage)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    run(Path(args.storage_root))
