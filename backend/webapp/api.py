import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .logging_config import configure_logging
from .metadata_store import MetadataStore, NewJobParams
from .migrate import run_migrations
from .models import JobRecord
from .object_store import build_upload_object_key, infer_mime_type, sanitize_object_key
from .queueing import enqueue_job
from .services import ServiceContainer, get_services
from .schemas import (
    ArtifactResponse,
    ChatAnswerResponse,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    CreateJobResponse,
    CreateJobRequest,
    JobDetail,
    JobSummary,
    PresignUploadRequest,
    PresignUploadResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
)


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_migrations()
    yield


app = FastAPI(title="Audio Summarizer API", version="0.1.0", lifespan=lifespan)

allow_origins = [item.strip() for item in settings.cors_allow_origins.split(",")] if settings.cors_allow_origins else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure tables exist even when called outside ASGI lifespan (tests/scripts).
run_migrations()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_metadata_store(db: Session = Depends(get_db)) -> MetadataStore:
    return MetadataStore(db)


def get_service_container() -> ServiceContainer:
    return get_services()


def _job_to_summary(job: JobRecord) -> JobSummary:
    return JobSummary(
        id=job.id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        status=job.status,
        source_type=job.source_type,
        source_url=job.source_url,
        title=job.title,
    )


def _job_to_detail(job: JobRecord) -> JobDetail:
    return JobDetail(
        id=job.id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        status=job.status,
        source_type=job.source_type,
        source_url=job.source_url,
        title=job.title,
        prefer_youtube_captions=job.prefer_youtube_captions,
        transcript_source=job.transcript_source,
        captions_attempted=job.captions_attempted,
        captions_status=job.captions_status,
        captions_detail=job.captions_detail,
        summary_object_key=job.summary_object_key,
        transcript_object_key=job.transcript_object_key,
        error=job.error,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/api/uploads/presign", response_model=PresignUploadResponse)
def presign_upload(payload: PresignUploadRequest) -> PresignUploadResponse:
    object_store = get_service_container().object_store
    object_key = build_upload_object_key(payload.filename)
    upload_url = object_store.generate_upload_url(object_key, payload.mime_type, settings.upload_url_expiry_seconds)
    return PresignUploadResponse(
        object_key=object_key,
        upload_url=upload_url,
        headers={"Content-Type": payload.mime_type},
    )


@app.put("/api/uploads/{object_key:path}")
async def upload_local_object(object_key: str, request: Request) -> dict:
    """
    Local-object-store upload fallback endpoint.
    For GCS presigned uploads, clients should PUT directly to GCS.
    """
    object_store = get_service_container().object_store
    object_key = sanitize_object_key(object_key)
    data = await request.body()
    content_type = request.headers.get("content-type") or infer_mime_type(object_key)
    object_store.put_bytes(object_key, data, content_type=content_type)
    return {"object_key": object_key, "size_bytes": len(data)}


@app.get("/api/artifacts/{object_key:path}")
def get_local_object(object_key: str) -> Response:
    object_store = get_service_container().object_store
    object_key = sanitize_object_key(object_key)
    if not object_store.exists(object_key):
        raise HTTPException(status_code=404, detail="Artifact not found")
    data = object_store.get_bytes(object_key)
    content_type = infer_mime_type(object_key)
    return Response(content=data, media_type=content_type)


@app.post("/api/jobs", response_model=CreateJobResponse)
def create_job(payload: CreateJobRequest, metadata: MetadataStore = Depends(get_metadata_store)) -> CreateJobResponse:
    has_youtube = bool(payload.youtube_url)
    has_upload = bool(payload.uploaded_object_key)
    if has_youtube == has_upload:
        raise HTTPException(status_code=400, detail="Provide exactly one of youtube_url or uploaded_object_key")

    source_type = "youtube" if has_youtube else "upload"
    job = metadata.create_job(
        NewJobParams(
            source_type=source_type,
            source_url=payload.youtube_url,
            uploaded_object_key=payload.uploaded_object_key,
            prefer_youtube_captions=payload.prefer_youtube_captions,
        )
    )
    if payload.uploaded_object_key:
        metadata.upsert_artifact(
            job.id,
            "upload",
            payload.uploaded_object_key,
            content_type=infer_mime_type(payload.uploaded_object_key),
        )

    try:
        enqueue_job(job.id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to enqueue job %s", job.id)
        metadata.update_job(job, status="failed", error=f"Failed to enqueue job: {exc}")
        raise HTTPException(status_code=503, detail="Queue unavailable; try again shortly.") from exc

    return CreateJobResponse(
        job_id=job.id,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@app.get("/api/jobs", response_model=list[JobSummary])
def list_jobs(metadata: MetadataStore = Depends(get_metadata_store)) -> list[JobSummary]:
    jobs = metadata.list_jobs()
    return [_job_to_summary(job) for job in jobs]


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobDetail:
    job = MetadataStore(db).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_detail(job)


def _artifact_from_job(
    job_id: str,
    object_key: str | None,
    local_filename: str,
    metadata: MetadataStore | None = None,
) -> ArtifactResponse:
    services = get_service_container()
    object_store = services.object_store
    if object_key and object_store.exists(object_key):
        text = object_store.get_text(object_key)
        if metadata:
            metadata.upsert_artifact(
                job_id,
                local_filename.replace(".txt", ""),
                object_key,
                size_bytes=len(text.encode("utf-8")),
                content_type="text/plain; charset=utf-8",
            )
        file_link = object_store.generate_download_url(object_key, settings.download_url_expiry_seconds)
        return ArtifactResponse(text=text, object_key=object_key, file_link=file_link)

    local_path = Path("storage") / job_id / local_filename
    if local_path.exists():
        text = local_path.read_text(encoding="utf-8")
        fallback_object_key = f"jobs/{job_id}/{local_filename}"
        if not object_store.exists(fallback_object_key):
            object_store.put_text(fallback_object_key, text)
        if metadata:
            metadata.upsert_artifact(
                job_id,
                local_filename.replace(".txt", ""),
                fallback_object_key,
                size_bytes=len(text.encode("utf-8")),
                content_type="text/plain; charset=utf-8",
            )
        file_link = object_store.generate_download_url(fallback_object_key, settings.download_url_expiry_seconds)
        return ArtifactResponse(text=text, object_key=fallback_object_key, file_link=file_link)
    raise HTTPException(status_code=404, detail=f"{local_filename} not found")


@app.get("/api/jobs/{job_id}/summary", response_model=ArtifactResponse)
def get_summary(job_id: str, db: Session = Depends(get_db)) -> ArtifactResponse:
    metadata = MetadataStore(db)
    job = metadata.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _artifact_from_job(job_id, job.summary_object_key, "summary.txt", metadata=metadata)


@app.get("/api/jobs/{job_id}/transcript", response_model=ArtifactResponse)
def get_transcript(job_id: str, db: Session = Depends(get_db)) -> ArtifactResponse:
    metadata = MetadataStore(db)
    job = metadata.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _artifact_from_job(job_id, job.transcript_object_key, "transcript.txt", metadata=metadata)


@app.get("/api/jobs/{job_id}/chat", response_model=ChatResponse)
def get_job_chat(job_id: str, db: Session = Depends(get_db)) -> ChatResponse:
    metadata = MetadataStore(db)
    if not metadata.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    rows = metadata.list_chat(job_id)
    messages = [ChatMessageOut(id=row.id, role=row.role, content=row.content, created_at=row.created_at) for row in rows]
    return ChatResponse(job_id=job_id, messages=messages)


@app.post("/api/jobs/{job_id}/chat", response_model=ChatAnswerResponse)
def post_job_chat(job_id: str, payload: ChatRequest, db: Session = Depends(get_db)) -> ChatAnswerResponse:
    metadata = MetadataStore(db)
    services = get_service_container()
    job = metadata.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    transcript = _artifact_from_job(job_id, job.transcript_object_key, "transcript.txt", metadata=metadata).text
    summary_text = None
    try:
        summary_text = _artifact_from_job(job_id, job.summary_object_key, "summary.txt", metadata=metadata).text
    except HTTPException:
        summary_text = None

    history_rows = metadata.list_chat(job_id)
    history_before = [{"role": row.role, "content": row.content} for row in history_rows]

    metadata.append_chat_message(job_id, "user", message)

    result = services.qa_service.answer_job_chat(
        message,
        transcript_text=transcript,
        conversation_history=history_before,
        summary_text=summary_text,
    )
    metadata.append_chat_message(job_id, "assistant", result.answer)

    return ChatAnswerResponse(answer=result.answer, context_stats=result.contexts)


@app.post("/api/search", response_model=SearchResponse)
def search(payload: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    services = get_service_container()
    object_store = services.object_store
    metadata = MetadataStore(db)
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    job_ids_filter: set[str] | None = None
    if payload.created_after or payload.created_before:
        stmt = select(JobRecord.id)
        if payload.created_after:
            stmt = stmt.where(JobRecord.created_at >= payload.created_after)
        if payload.created_before:
            stmt = stmt.where(JobRecord.created_at <= payload.created_before)
        job_ids_filter = {row[0] for row in db.execute(stmt).all()}
        if not job_ids_filter:
            return SearchResponse(answer="No matching jobs found for the selected date range.", hits=[])

    result = None
    if services.vector_store.client:
        try:
            query_embedding = services.vector_store._embed([question])[0]
            db_hits = metadata.query_vectors_by_embedding(
                query_embedding,
                top_k=payload.top_k,
                job_ids=job_ids_filter,
                source_url=payload.youtube_url,
            )
            if db_hits:
                result = services.qa_service.answer_from_records(question, db_hits)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB vector search failed, falling back to JSON index: %s", exc)

    if result is None:
        result = services.qa_service.answer(
            question,
            youtube_url=payload.youtube_url,
            top_k=payload.top_k,
            job_ids=job_ids_filter,
        )
    def _resolve_hit_file(job_id: str, kind: str, file_path: str | None) -> tuple[str | None, str | None]:
        object_key = f"jobs/{job_id}/{kind}.txt"
        if object_store.exists(object_key):
            return (object_key, object_store.generate_download_url(object_key, settings.download_url_expiry_seconds))
        if file_path:
            local_path = Path(file_path)
            if local_path.exists():
                try:
                    object_store.put_text(object_key, local_path.read_text(encoding="utf-8"))
                    return (object_key, object_store.generate_download_url(object_key, settings.download_url_expiry_seconds))
                except Exception:  # noqa: BLE001
                    return (None, str(local_path))
        return (None, None)

    hits: list[SearchHit] = []
    for hit in result.hits:
        object_key, file_link = _resolve_hit_file(hit.job_id, hit.kind, hit.file_path)
        hits.append(
            SearchHit(
                job_id=hit.job_id,
                kind=hit.kind,
                chunk_index=hit.chunk_index,
                file_path=hit.file_path,
                object_key=object_key,
                file_link=file_link,
                snippet=hit.text[:240],
            )
        )
    return SearchResponse(answer=result.answer, hits=hits)
