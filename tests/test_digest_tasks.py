from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.summarizer import DigestOverview
from backend.webapp import api as web_api
from backend.webapp import config as web_config
from backend.webapp import digest_tasks
from backend.webapp.db import Base
from backend.webapp.migrate import _ensure_digest_backfill_column
from backend.webapp.models import DigestPreference, DigestRun, JobRecord
from backend.webapp.services import get_services


@pytest.fixture
def digest_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "digest_test.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    web_config.settings.object_backend = "local"
    web_config.settings.local_object_root = tmp_path / "objects"
    web_config.settings.webapp_disable_auth = True
    web_config.settings.webapp_dev_user_email = "dev@example.com"
    web_config.settings.web_app_base_url = "http://app.test"
    web_config.settings.digest_profile_max_jobs = 5
    web_config.settings.digest_max_items_per_email = 2
    web_config.settings.digest_job_excerpt_chars = 120
    web_config.settings.digest_send_hour_local = 8
    web_config.settings.digest_weekly_weekday = 0

    monkeypatch.setattr(web_api, "SessionLocal", TestingSession)
    monkeypatch.setattr(web_api, "run_migrations", lambda: None)
    monkeypatch.setattr(digest_tasks, "SessionLocal", TestingSession)

    get_services.cache_clear()
    services = get_services()
    services.vector_store.client = None
    services.qa_service.vector_store.client = None

    captured: dict[str, object] = {"profile_count": 0, "overview_count": 0, "sent": None}

    def fake_profile(job_inputs):
        captured["profile_count"] = len(job_inputs)
        return "Profile summary"

    def fake_overview(job_inputs, cadence, profile_summary):
        captured["overview_count"] = len(job_inputs)
        return DigestOverview(
            intro=f"{cadence} digest intro",
            highlights=[job.title for job in job_inputs[:3]],
            profile_note=profile_summary,
        )

    monkeypatch.setattr(services.summarizer_client, "build_user_digest_profile", fake_profile)
    monkeypatch.setattr(services.summarizer_client, "build_digest_overview", fake_overview)

    class DummyNotifier:
        should_send = True

        def __init__(self, *_args, **_kwargs):
            pass

        def notify_digest(self, to_email, subject, text_body, html_body):
            captured["sent"] = {
                "to_email": to_email,
                "subject": subject,
                "text_body": text_body,
                "html_body": html_body,
            }
            return self.should_send

    monkeypatch.setattr(digest_tasks, "Notifier", DummyNotifier)

    yield {
        "session": TestingSession,
        "services": services,
        "captured": captured,
        "notifier_class": DummyNotifier,
        "db_path": db_path,
    }
    get_services.cache_clear()


def _create_completed_job(
    session,
    services,
    *,
    owner_email: str,
    title: str,
    completed_at: datetime,
    source_url: str | None = None,
) -> JobRecord:
    job = JobRecord(
        owner_email=owner_email,
        source_type="youtube",
        source_url=source_url or f"https://www.youtube.com/watch?v={title.replace(' ', '').lower()}",
        title=title,
        status="complete",
        created_at=completed_at - timedelta(minutes=5),
        updated_at=completed_at,
        completed_at=completed_at,
        summary_object_key=f"jobs/{title.replace(' ', '-').lower()}/summary.txt",
        transcript_object_key=f"jobs/{title.replace(' ', '-').lower()}/transcript.txt",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    services.object_store.put_text(job.summary_object_key, f"{title} summary excerpt")
    services.object_store.put_text(job.transcript_object_key, f"{title} transcript")
    return job


def test_first_enable_marks_historical_backfill_pending(digest_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(web_api, "_digest_delivery_status", lambda: (True, None))
    client = TestClient(web_api.app)

    response = client.put(
        "/api/digests/settings",
        json={"enabled": True, "cadence": "daily", "timezone": "America/New_York"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["historical_backfill_pending"] is True

    SessionLocal = digest_env["session"]
    with SessionLocal() as session:
        pref = session.get(DigestPreference, "dev@example.com")
        assert pref is not None
        assert pref.last_digest_cutoff_at is None
        assert pref.include_historical_on_next_send is True


def test_first_digest_includes_historical_jobs_and_marks_backfill_complete(digest_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(web_api, "_digest_delivery_status", lambda: (True, None))
    client = TestClient(web_api.app)
    owner_email = "dev@example.com"
    older = datetime.now(timezone.utc) - timedelta(days=7)

    SessionLocal = digest_env["session"]
    services = digest_env["services"]
    with SessionLocal() as session:
        _create_completed_job(session, services, owner_email=owner_email, title="Old One", completed_at=older)
        _create_completed_job(session, services, owner_email=owner_email, title="Old Two", completed_at=older + timedelta(hours=1))
        _create_completed_job(session, services, owner_email=owner_email, title="Old Three", completed_at=older + timedelta(hours=2))

    enable = client.put(
        "/api/digests/settings",
        json={"enabled": True, "cadence": "daily", "timezone": "America/New_York"},
    )
    assert enable.status_code == 200
    assert enable.json()["historical_backfill_pending"] is True

    result = digest_tasks.send_user_digest(owner_email)
    assert result == "sent"

    captured = digest_env["captured"]
    assert captured["profile_count"] == 3
    assert captured["overview_count"] == 3
    assert "Plus 1 more completed jobs in this digest window." in captured["sent"]["text_body"]
    assert "Plus 1 more completed jobs in this digest window." in captured["sent"]["html_body"]

    with SessionLocal() as session:
        pref = session.get(DigestPreference, owner_email)
        run = session.query(DigestRun).order_by(DigestRun.id.desc()).first()
        assert pref is not None
        assert pref.include_historical_on_next_send is False
        assert pref.last_digest_cutoff_at is not None
        assert run is not None
        assert run.status == "sent"
        assert run.job_count == 3
        assert json.loads(run.included_job_ids_json) and len(json.loads(run.included_job_ids_json)) == 3


def test_reenable_after_success_does_not_replay_backlog(digest_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(web_api, "_digest_delivery_status", lambda: (True, None))
    client = TestClient(web_api.app)
    owner_email = "dev@example.com"
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    SessionLocal = digest_env["session"]
    services = digest_env["services"]
    with SessionLocal() as session:
        session.add(
            DigestPreference(
                owner_email=owner_email,
                enabled=False,
                cadence="daily",
                timezone_name="America/New_York",
                send_hour_local=8,
                weekly_weekday=0,
                display_name="Dev User",
                last_digest_cutoff_at=cutoff,
                include_historical_on_next_send=False,
                last_sent_at=cutoff,
            )
        )
        session.commit()
        _create_completed_job(session, services, owner_email=owner_email, title="Before Cutoff", completed_at=cutoff - timedelta(hours=1))
        _create_completed_job(session, services, owner_email=owner_email, title="After Cutoff", completed_at=cutoff + timedelta(hours=1))

    response = client.put(
        "/api/digests/settings",
        json={"enabled": True, "cadence": "daily", "timezone": "America/New_York"},
    )
    assert response.status_code == 200
    assert response.json()["historical_backfill_pending"] is False

    result = digest_tasks.send_user_digest(owner_email)
    assert result == "sent"

    with SessionLocal() as session:
        run = session.query(DigestRun).order_by(DigestRun.id.desc()).first()
        pref = session.get(DigestPreference, owner_email)
        assert run is not None
        assert run.job_count == 1
        assert pref is not None
        assert pref.include_historical_on_next_send is False


def test_no_content_historical_backfill_clears_pending_state(digest_env):
    owner_email = "dev@example.com"
    SessionLocal = digest_env["session"]
    with SessionLocal() as session:
        session.add(
            DigestPreference(
                owner_email=owner_email,
                enabled=True,
                cadence="daily",
                timezone_name="UTC",
                send_hour_local=8,
                weekly_weekday=0,
                display_name="Dev User",
                last_digest_cutoff_at=None,
                include_historical_on_next_send=True,
            )
        )
        session.commit()

    result = digest_tasks.send_user_digest(owner_email)
    assert result == "skipped_no_content"

    with SessionLocal() as session:
        pref = session.get(DigestPreference, owner_email)
        run = session.query(DigestRun).order_by(DigestRun.id.desc()).first()
        assert pref is not None
        assert pref.include_historical_on_next_send is False
        assert pref.last_digest_cutoff_at is not None
        assert run is not None
        assert run.status == "skipped_no_content"


def test_failure_preserves_historical_backfill_pending(digest_env):
    owner_email = "dev@example.com"
    older = datetime.now(timezone.utc) - timedelta(days=3)
    SessionLocal = digest_env["session"]
    services = digest_env["services"]
    digest_env["notifier_class"].should_send = False

    with SessionLocal() as session:
        session.add(
            DigestPreference(
                owner_email=owner_email,
                enabled=True,
                cadence="daily",
                timezone_name="UTC",
                send_hour_local=8,
                weekly_weekday=0,
                display_name="Dev User",
                last_digest_cutoff_at=None,
                include_historical_on_next_send=True,
            )
        )
        session.commit()
        _create_completed_job(session, services, owner_email=owner_email, title="Retry Me", completed_at=older)

    result = digest_tasks.send_user_digest(owner_email)
    assert result == "failed"

    with SessionLocal() as session:
        pref = session.get(DigestPreference, owner_email)
        run = session.query(DigestRun).order_by(DigestRun.id.desc()).first()
        assert pref is not None
        assert pref.include_historical_on_next_send is True
        assert pref.last_digest_cutoff_at is None
        assert run is not None
        assert run.status == "failed"


def test_migration_backfills_legacy_digest_preferences(tmp_path: Path):
    db_path = tmp_path / "legacy_digest.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE digest_preferences (
                    owner_email TEXT PRIMARY KEY,
                    enabled BOOLEAN,
                    cadence TEXT,
                    timezone_name TEXT,
                    send_hour_local INTEGER,
                    weekly_weekday INTEGER,
                    display_name TEXT,
                    last_digest_cutoff_at TIMESTAMP,
                    last_run_at TIMESTAMP,
                    last_run_status TEXT,
                    last_sent_at TIMESTAMP,
                    next_send_at TIMESTAMP,
                    profile_summary TEXT,
                    profile_updated_at TIMESTAMP,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO digest_preferences (
                    owner_email, enabled, cadence, timezone_name, send_hour_local, weekly_weekday,
                    display_name, last_digest_cutoff_at, last_sent_at, created_at, updated_at
                ) VALUES (
                    'legacy@example.com', 1, 'daily', 'UTC', 8, 0, 'Legacy User',
                    '2026-03-10 12:00:00', NULL, '2026-03-10 12:00:00', '2026-03-10 12:00:00'
                )
                """
            )
        )
        _ensure_digest_backfill_column(conn, "sqlite")
        row = conn.execute(
            text(
                "SELECT include_historical_on_next_send FROM digest_preferences WHERE owner_email = 'legacy@example.com'"
            )
        ).first()
    assert row is not None
    assert int(row[0]) == 1
