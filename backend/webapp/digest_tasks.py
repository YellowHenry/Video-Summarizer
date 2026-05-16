from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from backend.notifier import Notifier
from backend.summarizer import DigestJobInput

from .artifacts import load_text_artifact
from .config import settings
from .db import SessionLocal
from .digest_render import build_digest_subject, render_digest_html, render_digest_text
from .metadata_store import MetadataStore
from .queueing import get_queue, get_redis_connection
from .services import get_services


logger = logging.getLogger(__name__)


def normalize_timezone(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "UTC"
    try:
        ZoneInfo(raw)
        return raw
    except Exception:
        return "UTC"


def compute_next_send_at(
    cadence: str,
    timezone_name: str,
    now_utc: datetime,
    send_hour_local: int = 8,
    weekly_weekday: int = 0,
) -> datetime:
    tz = ZoneInfo(normalize_timezone(timezone_name))
    now_local = now_utc.astimezone(tz)
    target_time = time(hour=max(0, min(23, int(send_hour_local))), minute=0)

    if cadence == "weekly":
        days_ahead = (int(weekly_weekday) - now_local.weekday()) % 7
        candidate_date = now_local.date() + timedelta(days=days_ahead)
        candidate = datetime.combine(candidate_date, target_time, tzinfo=tz)
        if candidate <= now_local:
            candidate += timedelta(days=7)
        return candidate.astimezone(timezone.utc)

    candidate = datetime.combine(now_local.date(), target_time, tzinfo=tz)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def enqueue_digest_sweep() -> str:
    if settings.sync_jobs:
        run_due_digests()
        return "sync-digest-sweep"

    conn = get_redis_connection()
    lock = conn.lock("digest-sweep-enqueue-lock", timeout=30)
    acquired = lock.acquire(blocking=False)
    if not acquired:
        logger.info("Digest sweep enqueue skipped; another enqueue is already in flight.")
        return "already-enqueued"
    try:
        queue = get_queue()
        rq_job = queue.enqueue(
            "backend.webapp.digest_tasks.run_due_digests",
            job_timeout="30m",
            result_ttl=3600,
            failure_ttl=24 * 3600,
        )
        return rq_job.id
    finally:
        try:
            lock.release()
        except Exception:
            pass


def run_due_digests() -> int:
    if settings.sync_jobs:
        session = SessionLocal()
        try:
            metadata = MetadataStore(session)
            due_rows = metadata.list_due_digest_preferences(datetime.now(timezone.utc))
            due_emails = [row.owner_email for row in due_rows]
        finally:
            session.close()
        sent = 0
        for owner_email in due_emails:
            send_user_digest(owner_email)
            sent += 1
        return sent

    conn = get_redis_connection()
    timeout_seconds = max(600, int(settings.digest_sweep_interval_minutes) * 60)
    lock = conn.lock("digest-sweep-lock", timeout=timeout_seconds)
    if not lock.acquire(blocking=False):
        logger.info("Digest sweep skipped; another sweep is already running.")
        return 0

    try:
        session = SessionLocal()
        try:
            metadata = MetadataStore(session)
            due_rows = metadata.list_due_digest_preferences(datetime.now(timezone.utc))
            due_emails = [row.owner_email for row in due_rows]
        finally:
            session.close()

        sent = 0
        for owner_email in due_emails:
            send_user_digest(owner_email)
            sent += 1
        return sent
    finally:
        try:
            lock.release()
        except Exception:
            pass


def _build_digest_job_input(metadata: MetadataStore, object_store, job) -> DigestJobInput:
    summary_excerpt = ""
    try:
        summary_text, _ = load_text_artifact(job, "summary", metadata, object_store)
        excerpt_len = max(80, int(settings.digest_job_excerpt_chars))
        summary_excerpt = " ".join(summary_text.strip().split())[:excerpt_len].strip()
    except Exception:
        summary_excerpt = ""

    return DigestJobInput(
        title=(job.title or job.source_url or "(untitled job)").strip(),
        source_url=job.source_url,
        source_type=job.source_type,
        transcript_source=job.transcript_source,
        summary_excerpt=summary_excerpt,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )


def send_user_digest(owner_email: str) -> str:
    session = SessionLocal()
    metadata = MetadataStore(session)
    services = get_services()
    notifier = Notifier()
    now = datetime.now(timezone.utc)
    status = "failed"
    window_start: datetime | None = None

    try:
        pref = metadata.get_digest_preference(owner_email)
        if not pref or not pref.enabled:
            return "disabled"

        window_start = None if pref.include_historical_on_next_send else pref.last_digest_cutoff_at
        window_end = now
        next_send_at = compute_next_send_at(
            pref.cadence,
            pref.timezone_name,
            now,
            send_hour_local=pref.send_hour_local,
            weekly_weekday=pref.weekly_weekday,
        )
        window_jobs = metadata.list_completed_jobs_in_window(owner_email, window_start, window_end)

        if not window_jobs:
            metadata.create_digest_run(
                owner_email=owner_email,
                cadence=pref.cadence,
                status="skipped_no_content",
                window_start_at=window_start,
                window_end_at=window_end,
                job_count=0,
            )
            metadata.upsert_digest_preference(
                owner_email,
                enabled=True,
                cadence=pref.cadence,
                timezone_name=pref.timezone_name,
                send_hour_local=pref.send_hour_local,
                weekly_weekday=pref.weekly_weekday,
                display_name=pref.display_name,
                last_run_at=now,
                last_run_status="skipped_no_content",
                last_digest_cutoff_at=now,
                include_historical_on_next_send=False,
                next_send_at=next_send_at,
                profile_summary=pref.profile_summary,
                profile_updated_at=pref.profile_updated_at,
                last_sent_at=pref.last_sent_at,
            )
            return "skipped_no_content"

        profile_jobs = metadata.list_completed_jobs_for_profile(owner_email, settings.digest_profile_max_jobs)
        if not profile_jobs:
            profile_jobs = window_jobs

        profile_inputs = [_build_digest_job_input(metadata, services.object_store, job) for job in profile_jobs]
        profile_summary = services.summarizer_client.build_user_digest_profile(profile_inputs)
        profile_updated_at = now if profile_summary else pref.profile_updated_at

        overview_job_limit = max(1, int(settings.digest_profile_max_jobs))
        overview_jobs = window_jobs[:overview_job_limit]
        max_items = max(1, int(settings.digest_max_items_per_email))
        included_jobs = window_jobs[:max_items]
        remaining_job_count = max(0, len(window_jobs) - len(included_jobs))
        overview_inputs = [_build_digest_job_input(metadata, services.object_store, job) for job in overview_jobs]
        digest_inputs = [_build_digest_job_input(metadata, services.object_store, job) for job in included_jobs]
        overview = services.summarizer_client.build_digest_overview(overview_inputs, pref.cadence, profile_summary)

        app_base_url = settings.web_app_base_url.rstrip("/")
        rendered_jobs: list[dict] = []
        for job, digest_input in zip(included_jobs, digest_inputs, strict=False):
            rendered_jobs.append(
                {
                    "job_id": job.id,
                    "title": digest_input.title,
                    "summary_excerpt": digest_input.summary_excerpt,
                    "completed_at": job.completed_at,
                    "source_url": job.source_url,
                    "app_link": f"{app_base_url}/?job={job.id}&tab=summary",
                }
            )

        subject = build_digest_subject(pref.cadence, len(window_jobs))
        text_body = render_digest_text(
            recipient_name=pref.display_name,
            cadence=pref.cadence,
            overview=overview,
            profile_summary=profile_summary,
            jobs=rendered_jobs,
            app_url=app_base_url,
            timezone_name=pref.timezone_name,
            remaining_job_count=remaining_job_count,
        )
        html_body = render_digest_html(
            recipient_name=pref.display_name,
            overview=overview,
            profile_summary=profile_summary,
            jobs=rendered_jobs,
            app_url=app_base_url,
            timezone_name=pref.timezone_name,
            remaining_job_count=remaining_job_count,
        )

        sent = notifier.notify_digest(owner_email, subject, text_body, html_body)
        if not sent:
            raise RuntimeError("SMTP delivery failed or is not configured")

        metadata.create_digest_run(
            owner_email=owner_email,
            cadence=pref.cadence,
            status="sent",
            window_start_at=window_start,
            window_end_at=window_end,
            job_count=len(window_jobs),
            included_job_ids_json=json.dumps([job.id for job in window_jobs]),
            subject=subject,
            body_text=text_body,
            body_html=html_body,
            profile_summary_snapshot=profile_summary,
            sent_at=now,
        )
        metadata.upsert_digest_preference(
            owner_email,
            enabled=True,
            cadence=pref.cadence,
            timezone_name=pref.timezone_name,
            send_hour_local=pref.send_hour_local,
            weekly_weekday=pref.weekly_weekday,
            display_name=pref.display_name,
            last_run_at=now,
            last_run_status="sent",
            last_sent_at=now,
            last_digest_cutoff_at=now,
            include_historical_on_next_send=False,
            next_send_at=next_send_at,
            profile_summary=profile_summary,
            profile_updated_at=profile_updated_at,
        )
        status = "sent"
        return status
    except Exception as exc:  # noqa: BLE001
        logger.exception("Digest send failed for %s", owner_email)
        pref = metadata.get_digest_preference(owner_email)
        cadence = pref.cadence if pref else "daily"
        metadata.create_digest_run(
            owner_email=owner_email,
            cadence=cadence,
            status="failed",
            window_start_at=window_start if pref else None,
            window_end_at=now,
            job_count=0,
            error=str(exc),
        )
        if pref:
            metadata.upsert_digest_preference(
                owner_email,
                enabled=pref.enabled,
                cadence=pref.cadence,
                timezone_name=pref.timezone_name,
                send_hour_local=pref.send_hour_local,
                weekly_weekday=pref.weekly_weekday,
                display_name=pref.display_name,
                last_run_at=now,
                last_run_status="failed",
                last_sent_at=pref.last_sent_at,
                last_digest_cutoff_at=pref.last_digest_cutoff_at,
                include_historical_on_next_send=pref.include_historical_on_next_send,
                next_send_at=pref.next_send_at,
                profile_summary=pref.profile_summary,
                profile_updated_at=pref.profile_updated_at,
            )
        return status
    finally:
        session.close()
