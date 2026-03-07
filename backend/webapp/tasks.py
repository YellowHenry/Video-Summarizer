import logging
import os
from pathlib import Path
from types import SimpleNamespace

from backend.compression import CompressionConfig, Compressor
from backend.downloader import AudioDownloader
from backend.storage import Storage
from backend.summarizer import CloudSummarizerClient
from backend.vector_store import VectorStore

from .db import SessionLocal
from .metadata_store import MetadataStore
from .models import JobRecord
from .object_store import get_object_store
from .queueing import get_redis_connection


logger = logging.getLogger(__name__)
TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "temporarily unavailable",
    "service unavailable",
    "http 429",
    "too many requests",
    "rate limit",
    "connection reset",
    "connection aborted",
    "connection refused",
    "connection error",
    "dns",
    "network is unreachable",
)
YOUTUBE_BOT_BLOCK_MARKERS = (
    "sign in to confirm you",
    "requestblocked",
    "ipblocked",
    "youtube is blocking requests from your ip",
    "use --cookies-from-browser or --cookies",
)
COOKIE_AUTH_REQUIRED_MARKERS = (
    "cookie auth is required",
    "ytdlp_strict_cookies=true",
    "cookie_auth_required",
)


def _update_job_status(job: JobRecord, session, status: str, error: str | None = None) -> None:
    job.status = status
    job.error = error
    session.add(job)
    session.commit()
    session.refresh(job)


def _acquire_job_lock(job_id: str):
    """
    Acquire a best-effort Redis lock to avoid duplicate processing.
    Returns:
      - lock object when acquired
      - None if another worker already holds the lock
      - "no_lock" sentinel when lock acquisition is unavailable (continue anyway)
    """
    try:
        conn = get_redis_connection()
        lock = conn.lock(f"job-lock:{job_id}", timeout=6 * 3600)
        if not lock.acquire(blocking=False):
            return None
        return lock
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to acquire Redis lock for job %s; proceeding unlocked: %s", job_id, exc)
        return "no_lock"


def _release_job_lock(lock, job_id: str) -> None:
    if not lock or lock == "no_lock":
        return
    try:
        lock.release()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to release job lock for %s: %s", job_id, exc)


def _remaining_retry_attempts() -> int:
    try:
        from rq import get_current_job

        rq_job = get_current_job()
        if not rq_job:
            return 0
        retries_left = getattr(rq_job, "retries_left", None)
        if retries_left is None:
            return 0
        return int(retries_left)
    except Exception:
        return 0


def _is_transient_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in TRANSIENT_ERROR_MARKERS)


def _is_youtube_bot_block_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in YOUTUBE_BOT_BLOCK_MARKERS)


def _is_cookie_auth_required_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in COOKIE_AUTH_REQUIRED_MARKERS)


def _has_youtube_auth_config() -> bool:
    auth_vars = (
        "YTDLP_COOKIES",
        "YTDLP_COOKIES_TEXT",
        "YTDLP_COOKIES_B64",
        "YTDLP_COOKIES_FROM_BROWSER",
        "YTDLP_PROXY",
        "PROXY_ENABLED",
        "PROXY_CAPTIONS_ONLY",
        "PROXY_POOL",
    )
    return any(bool(os.getenv(name, "").strip()) for name in auth_vars)


def process_job(job_id: str) -> None:
    """
    RQ worker entrypoint. Runs the same core summarization pipeline used by desktop,
    then persists artifacts in object storage + metadata in Postgres.
    """
    session = SessionLocal()
    metadata = MetadataStore(session)
    storage = Storage()
    object_store = get_object_store()
    downloader = AudioDownloader()
    summarizer = CloudSummarizerClient()
    vector_store = VectorStore()
    lock = _acquire_job_lock(job_id)
    if lock is None:
        logger.info("Skipping duplicate worker execution for job %s (lock already held)", job_id)
        session.close()
        return

    try:
        job = session.get(JobRecord, job_id)
        if not job:
            logger.error("Job not found: %s", job_id)
            return
        if job.status == "complete" and job.summary_object_key:
            logger.info("Skipping already completed job %s", job_id)
            return

        summarize_result = None
        skip_caption_retry_in_summarize = False

        if job.source_type == "youtube":
            if not job.source_url:
                raise ValueError("YouTube job is missing source_url")
            if not job.title:
                title_lookup = getattr(downloader, "get_youtube_title", None)
                if callable(title_lookup):
                    title = title_lookup(job.source_url)
                    if title:
                        metadata.update_job(job, title=title)
            if job.prefer_youtube_captions:
                _update_job_status(job, session, "summarizing")
                try:
                    summarize_result = summarizer.summarize_youtube_captions_only(job.source_url)
                    logger.info("Caption-first summarization succeeded for job %s", job.id)
                except Exception as exc:  # noqa: BLE001
                    if _is_cookie_auth_required_error(exc):
                        job.captions_attempted = True
                        job.captions_status = "cookie_auth_required"
                        job.captions_detail = str(exc)
                        session.add(job)
                        session.commit()
                        raise RuntimeError(str(exc)) from exc
                    if _is_youtube_bot_block_error(exc) and not _has_youtube_auth_config():
                        job.captions_attempted = True
                        job.captions_status = "blocked_cloud_ip"
                        job.captions_detail = str(exc)
                        session.add(job)
                        session.commit()
                        raise RuntimeError(
                            "YouTube blocked this Cloud Run worker IP (bot-check). "
                            "Set YTDLP_COOKIES_FILE in infra/gcp/deploy_config.py (or YTDLP_COOKIES_B64/YTDLP_PROXY), "
                            "redeploy the worker, and retry."
                        ) from exc
                    logger.info(
                        "Caption-first summarization unavailable for job %s: %s",
                        job.id,
                        exc,
                    )
                    skip_caption_retry_in_summarize = True
                    allow_whisper_fallback = True if job.allow_whisper_fallback is None else bool(job.allow_whisper_fallback)
                    if not allow_whisper_fallback:
                        job.captions_attempted = True
                        job.captions_status = "fallback_disabled"
                        job.captions_detail = str(exc)
                        session.add(job)
                        session.commit()
                        _update_job_status(
                            job,
                            session,
                            "failed",
                            f"YouTube captions unavailable and Whisper fallback is disabled: {exc}",
                        )
                        return
                    logger.info("Whisper fallback enabled for job %s; switching to media download path.", job.id)

            if summarize_result is None:
                _update_job_status(job, session, "downloading")
                source_media, title = downloader.download_youtube(job.source_url)
                if title:
                    metadata.update_job(job, title=title)
        else:
            if not job.uploaded_object_key:
                raise ValueError("Upload job is missing uploaded_object_key")
            _update_job_status(job, session, "downloading")
            source_media = object_store.download_to_temp(job.uploaded_object_key)

        if summarize_result is None:
            _update_job_status(job, session, "preprocessing")
            compressed = Compressor(CompressionConfig()).compress(source_media)

            _update_job_status(job, session, "summarizing")
            summarize_result = summarizer.summarize(
                compressed,
                youtube_url=job.source_url if job.source_type == "youtube" else None,
                prefer_youtube_captions=bool(job.prefer_youtube_captions and not skip_caption_retry_in_summarize),
            )

        job.transcript_source = summarize_result.transcript_source
        job.captions_attempted = summarize_result.captions_attempted
        job.captions_status = summarize_result.captions_status
        job.captions_detail = summarize_result.captions_detail
        session.add(job)
        session.commit()

        stub_prefix = "it seems that the transcript you intended to provide is missing"
        is_stub = summarize_result.summary.strip().lower().startswith(stub_prefix)
        if is_stub:
            _update_job_status(job, session, "failed", "Transcript missing; summary was placeholder and removed.")
            return

        # Keep local compatibility artifacts under storage/<job_id>/ for current tools.
        local_summary_path = storage.store_summary(job.id, summarize_result.summary)
        if job.title:
            storage.store_title(job.id, job.title)
        local_transcript_path: Path | None = None
        if summarize_result.transcript:
            local_transcript_path = storage.store_transcript(job.id, summarize_result.transcript)

        # Persist summary/transcript to object storage for web APIs.
        summary_key = f"jobs/{job.id}/summary.txt"
        object_store.put_text(summary_key, summarize_result.summary)
        job.summary_object_key = summary_key
        try:
            summary_bytes = len(summarize_result.summary.encode("utf-8"))
        except Exception:
            summary_bytes = None
        metadata.upsert_artifact(
            job.id,
            "summary",
            summary_key,
            size_bytes=summary_bytes,
            content_type="text/plain; charset=utf-8",
        )
        if summarize_result.transcript:
            transcript_key = f"jobs/{job.id}/transcript.txt"
            object_store.put_text(transcript_key, summarize_result.transcript)
            job.transcript_object_key = transcript_key
            try:
                transcript_bytes = len(summarize_result.transcript.encode("utf-8"))
            except Exception:
                transcript_bytes = None
            metadata.upsert_artifact(
                job.id,
                "transcript",
                transcript_key,
                size_bytes=transcript_bytes,
                content_type="text/plain; charset=utf-8",
            )

        # Save desktop-style metadata snapshot as well.
        meta_job = SimpleNamespace(
            id=job.id,
            created_at=job.created_at.timestamp() if hasattr(job.created_at, "timestamp") else None,
            youtube_url=job.source_url,
            audio_path=None,
            title=job.title,
            display_name=job.title,
            status="complete",
            prefer_youtube_captions=job.prefer_youtube_captions,
            allow_whisper_fallback=True if job.allow_whisper_fallback is None else bool(job.allow_whisper_fallback),
            transcript_source=job.transcript_source,
            captions_attempted=job.captions_attempted,
            captions_status=job.captions_status,
            captions_detail=job.captions_detail,
        )
        storage.store_metadata(meta_job)

        # Index vectors for global search path.
        if vector_store.client:
            try:
                vector_store.remove_job_records(job.id)
                vector_store.add_text(
                    job.id,
                    summarize_result.summary,
                    job.source_url,
                    kind="summary",
                    file_path=local_summary_path,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to index summary for job %s: %s", job.id, exc)
            if summarize_result.transcript and local_transcript_path:
                try:
                    vector_store.add_text(
                        job.id,
                        summarize_result.transcript,
                        job.source_url,
                        kind="transcript",
                        file_path=local_transcript_path,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to index transcript for job %s: %s", job.id, exc)
            try:
                job_records = [record for record in vector_store.records if record.job_id == job.id]
                metadata.replace_vectors(job.id, job_records)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to sync vectors table for job %s: %s", job.id, exc)
        else:
            logger.info(
                "Skipping vector indexing for job %s because OpenAI key is not configured "
                "(set backend/config.py OPENAI_API_KEY or env OPENAI_API_KEY).",
                job.id,
            )
            try:
                metadata.replace_vectors(job.id, [])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to clear vectors table for non-indexed job %s: %s", job.id, exc)

        job.error = None
        _update_job_status(job, session, "complete")
        session.add(job)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s failed in worker", job_id)
        job = session.get(JobRecord, job_id)
        retries_left = _remaining_retry_attempts()
        should_retry = retries_left > 0 and _is_transient_error(exc)
        if job and should_retry:
            _update_job_status(
                job,
                session,
                "queued",
                f"Transient failure (retrying, attempts_left={retries_left}): {exc}",
            )
        elif job:
            _update_job_status(job, session, "failed", str(exc))
        if should_retry:
            raise
    finally:
        session.close()
        _release_job_lock(lock, job_id)
