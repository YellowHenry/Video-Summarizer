from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.qa import QAResult
from backend.summarizer import SummarizeResult
from backend.vector_store import VectorRecord
from backend.webapp import api as web_api
from backend.webapp import config as web_config
from backend.webapp import tasks as web_tasks
from backend.webapp.db import Base
from backend.webapp.models import JobArtifact, JobRecord
from backend.webapp.services import get_services


@pytest.fixture
def web_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "webapp_test.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    web_config.settings.object_backend = "local"
    web_config.settings.local_object_root = tmp_path / "objects"
    web_config.settings.api_base_url = "http://testserver"

    monkeypatch.setattr(web_api, "SessionLocal", TestingSession)
    monkeypatch.setattr(web_tasks, "SessionLocal", TestingSession)
    monkeypatch.setattr(web_api, "run_migrations", lambda: None)
    monkeypatch.setattr(web_tasks, "run_migrations", lambda: None)

    get_services.cache_clear()
    services = get_services()
    services.vector_store.client = None
    services.qa_service.vector_store.client = None
    yield {"session": TestingSession, "tmp_path": tmp_path}
    get_services.cache_clear()


def _patch_worker_deps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_downloader: bool = True) -> None:
    media_path = tmp_path / "input.m4a"
    media_path.write_bytes(b"audio-bytes")

    class DummyDownloader:
        def download_youtube(self, url: str):
            return media_path, "Mock title"

    class DummyCompressor:
        def __init__(self, *_args, **_kwargs):
            pass

        def compress(self, source_media: Path) -> Path:
            return source_media

    class DummySummarizer:
        def summarize(self, *_args, **_kwargs) -> SummarizeResult:
            return SummarizeResult(
                summary="Mock summary",
                transcript="Mock transcript",
                transcript_source="whisper",
                captions_attempted=False,
                captions_status="skipped",
                captions_detail=None,
            )

    class DummyVectorStore:
        def __init__(self):
            self.client = False
            self.records = []

        def remove_job_records(self, _job_id: str):
            return 0

        def add_text(self, *_args, **_kwargs):
            return None

    if with_downloader:
        monkeypatch.setattr(web_tasks, "AudioDownloader", lambda: DummyDownloader())
    monkeypatch.setattr(web_tasks, "Compressor", lambda *_args, **_kwargs: DummyCompressor())
    monkeypatch.setattr(web_tasks, "CloudSummarizerClient", lambda *_args, **_kwargs: DummySummarizer())
    monkeypatch.setattr(web_tasks, "VectorStore", lambda *_args, **_kwargs: DummyVectorStore())
    monkeypatch.setattr(web_tasks, "_acquire_job_lock", lambda _job_id: "no_lock")
    monkeypatch.setattr(web_tasks, "_release_job_lock", lambda _lock, _job_id: None)


def test_job_creation_and_enqueue(web_env, monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_enqueue(job_id: str) -> str:
        captured["job_id"] = job_id
        return "queued-1"

    monkeypatch.setattr(web_api, "enqueue_job", fake_enqueue)
    client = TestClient(web_api.app)

    response = client.post(
        "/api/jobs",
        json={"youtube_url": "https://www.youtube.com/watch?v=abc123", "prefer_youtube_captions": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == captured["job_id"]
    assert payload["status"] == "queued"

    SessionLocal = web_env["session"]
    with SessionLocal() as session:
        row = session.get(JobRecord, payload["job_id"])
        assert row is not None
        assert row.status == "queued"


def test_chat_persistence_round_trip(web_env, monkeypatch: pytest.MonkeyPatch):
    services = get_services()
    SessionLocal = web_env["session"]

    with SessionLocal() as session:
        job = JobRecord(
            source_type="youtube",
            source_url="https://www.youtube.com/watch?v=chat1",
            status="complete",
            summary_object_key="jobs/chatjob/summary.txt",
            transcript_object_key="jobs/chatjob/transcript.txt",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    services.object_store.put_text("jobs/chatjob/summary.txt", "Summary")
    services.object_store.put_text("jobs/chatjob/transcript.txt", "Transcript for chat testing.")

    monkeypatch.setattr(
        services.qa_service,
        "answer_job_chat",
        lambda *_args, **_kwargs: QAResult(answer="Assistant response", contexts=["ctx"], hits=[]),
    )

    client = TestClient(web_api.app)
    post_response = client.post(f"/api/jobs/{job_id}/chat", json={"message": "What happened?"})
    assert post_response.status_code == 200
    assert post_response.json()["answer"] == "Assistant response"

    get_response = client.get(f"/api/jobs/{job_id}/chat")
    assert get_response.status_code == 200
    messages = get_response.json()["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_job_chat_uses_full_transcript_and_prior_history(web_env, monkeypatch: pytest.MonkeyPatch):
    services = get_services()
    SessionLocal = web_env["session"]

    with SessionLocal() as session:
        job = JobRecord(
            source_type="youtube",
            source_url="https://www.youtube.com/watch?v=chatctx",
            status="complete",
            summary_object_key="jobs/chatctx/summary.txt",
            transcript_object_key="jobs/chatctx/transcript.txt",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    services.object_store.put_text("jobs/chatctx/summary.txt", "Summary context")
    services.object_store.put_text("jobs/chatctx/transcript.txt", "Transcript context")

    captured: dict = {}

    def fake_answer(question: str, transcript_text: str, conversation_history=None, summary_text=None):
        captured["question"] = question
        captured["transcript_text"] = transcript_text
        captured["summary_text"] = summary_text
        captured["history"] = list(conversation_history or [])
        return QAResult(answer=f"Answer for {question}", contexts=["ctx"], hits=[])

    monkeypatch.setattr(services.qa_service, "answer_job_chat", fake_answer)
    client = TestClient(web_api.app)

    first = client.post(f"/api/jobs/{job_id}/chat", json={"message": "First question"})
    assert first.status_code == 200
    second = client.post(f"/api/jobs/{job_id}/chat", json={"message": "Second question"})
    assert second.status_code == 200

    assert captured["question"] == "Second question"
    assert captured["transcript_text"] == "Transcript context"
    assert captured["summary_text"] == "Summary context"
    # Before answering second message, history should include prior user/assistant pair.
    assert len(captured["history"]) == 2
    assert captured["history"][0]["role"] == "user"
    assert captured["history"][0]["content"] == "First question"
    assert captured["history"][1]["role"] == "assistant"
    assert captured["history"][1]["content"] == "Answer for First question"


def test_worker_state_transitions_and_artifacts(web_env, monkeypatch: pytest.MonkeyPatch):
    _patch_worker_deps(monkeypatch, web_env["tmp_path"], with_downloader=True)
    SessionLocal = web_env["session"]

    with SessionLocal() as session:
        job = JobRecord(
            source_type="youtube",
            source_url="https://www.youtube.com/watch?v=worker1",
            status="queued",
            prefer_youtube_captions=True,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    web_tasks.process_job(job_id)

    with SessionLocal() as session:
        row = session.get(JobRecord, job_id)
        assert row is not None
        assert row.status == "complete"
        assert row.transcript_source == "whisper"
        assert row.captions_status == "skipped"
        assert row.summary_object_key is not None
        assert row.transcript_object_key is not None
        artifacts = session.query(JobArtifact).filter(JobArtifact.job_id == job_id).all()
        kinds = sorted(item.kind for item in artifacts)
        assert kinds == ["summary", "transcript"]


def test_worker_transient_failure_requeues(web_env, monkeypatch: pytest.MonkeyPatch):
    class FailingDownloader:
        def download_youtube(self, _url: str):
            raise RuntimeError("connection timeout to upstream")

    monkeypatch.setattr(web_tasks, "AudioDownloader", lambda: FailingDownloader())
    monkeypatch.setattr(web_tasks, "_acquire_job_lock", lambda _job_id: "no_lock")
    monkeypatch.setattr(web_tasks, "_release_job_lock", lambda _lock, _job_id: None)
    monkeypatch.setattr(web_tasks, "_remaining_retry_attempts", lambda: 2)
    monkeypatch.setattr(web_tasks, "_is_transient_error", lambda _exc: True)

    SessionLocal = web_env["session"]
    with SessionLocal() as session:
        job = JobRecord(
            source_type="youtube",
            source_url="https://www.youtube.com/watch?v=retriable",
            status="queued",
            prefer_youtube_captions=True,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    with pytest.raises(RuntimeError):
        web_tasks.process_job(job_id)

    with SessionLocal() as session:
        row = session.get(JobRecord, job_id)
        assert row is not None
        assert row.status == "queued"
        assert row.error is not None
        assert "Transient failure" in row.error


def test_integration_upload_to_summary_retrieval(web_env, monkeypatch: pytest.MonkeyPatch):
    _patch_worker_deps(monkeypatch, web_env["tmp_path"], with_downloader=False)

    def fake_enqueue(job_id: str) -> str:
        web_tasks.process_job(job_id)
        return "sync-job"

    monkeypatch.setattr(web_api, "enqueue_job", fake_enqueue)
    client = TestClient(web_api.app)

    presign = client.post("/api/uploads/presign", json={"filename": "demo.mp3", "mime_type": "audio/mpeg"})
    assert presign.status_code == 200
    presign_payload = presign.json()
    upload_path = urlsplit(presign_payload["upload_url"]).path
    put_response = client.put(upload_path, content=b"upload-bytes", headers={"Content-Type": "audio/mpeg"})
    assert put_response.status_code == 200

    created = client.post(
        "/api/jobs",
        json={
            "uploaded_object_key": presign_payload["object_key"],
            "prefer_youtube_captions": True,
        },
    )
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    summary_response = client.get(f"/api/jobs/{job_id}/summary")
    transcript_response = client.get(f"/api/jobs/{job_id}/transcript")
    assert summary_response.status_code == 200
    assert transcript_response.status_code == 200
    assert summary_response.json()["text"] == "Mock summary"
    assert transcript_response.json()["text"] == "Mock transcript"

    SessionLocal = web_env["session"]
    with SessionLocal() as session:
        artifacts = session.query(JobArtifact).filter(JobArtifact.job_id == job_id).all()
        kinds = sorted(item.kind for item in artifacts)
        assert kinds == ["summary", "transcript", "upload"]


def test_search_returns_chunk_links(web_env, monkeypatch: pytest.MonkeyPatch):
    services = get_services()
    local_file = web_env["tmp_path"] / "hit_transcript.txt"
    local_file.write_text("A baseball discussion snippet from transcript.", encoding="utf-8")

    hit = VectorRecord(
        text="A baseball discussion snippet from transcript.",
        job_id="job123",
        source_url="https://youtube.com/watch?v=job123",
        chunk_index=0,
        kind="transcript",
        file_path=str(local_file),
        embedding=[0.1, 0.2, 0.3],
    )

    monkeypatch.setattr(
        services.qa_service,
        "answer",
        lambda *_args, **_kwargs: QAResult(answer="Search answer", contexts=["ctx"], hits=[hit]),
    )

    client = TestClient(web_api.app)
    response = client.post("/api/search", json={"question": "What about baseball?"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Search answer"
    assert len(payload["hits"]) == 1
    assert payload["hits"][0]["file_link"] is not None
