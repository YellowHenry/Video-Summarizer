import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.notifier import Notifier

from .auth import AuthUser, owner_key_from_email, require_user
from .artifacts import load_text_artifact
from .config import settings
from .db import SessionLocal
from .digest_tasks import compute_next_send_at, enqueue_digest_sweep, normalize_timezone
from .logging_config import configure_logging
from .metadata_store import MetadataStore, NewJobParams
from .migrate import run_migrations
from .models import JobRecord
from .object_store import build_upload_object_key, infer_mime_type, sanitize_object_key
from .queueing import enqueue_job
from .services import ServiceContainer, get_services
from .schemas import (
    AuthMeResponse,
    ArtifactResponse,
    ChatAnswerResponse,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    CreateJobResponse,
    CreateJobRequest,
    DigestRunOut,
    DigestSettingsResponse,
    JobDetail,
    JobSummary,
    PresignUploadRequest,
    PresignUploadResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
    UpdateDigestSettingsRequest,
)
from .title_resolver import looks_like_url, resolve_display_title


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


def _normalize_owner_email(email: str) -> str:
    return email.strip().lower()


def _assert_upload_key_owner(object_key: str, owner_email: str) -> None:
    owner_prefix = f"uploads/{owner_key_from_email(owner_email)}/"
    if not object_key.startswith(owner_prefix):
        raise HTTPException(status_code=404, detail="Artifact not found")


def _assert_object_access(object_key: str, owner_email: str, metadata: MetadataStore) -> None:
    owner_prefix = f"uploads/{owner_key_from_email(owner_email)}/"
    if object_key.startswith(owner_prefix):
        return

    if object_key.startswith("jobs/"):
        parts = object_key.split("/", 2)
        if len(parts) >= 3:
            job_id = parts[1]
            if metadata.get_job(job_id, owner_email):
                return

    if metadata.has_object_access(object_key, owner_email):
        return

    raise HTTPException(status_code=404, detail="Artifact not found")


def _job_to_summary(job: JobRecord, resolved_title: str | None = None) -> JobSummary:
    return JobSummary(
        id=job.id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        status=job.status,
        source_type=job.source_type,
        source_url=job.source_url,
        title=resolved_title if resolved_title is not None else job.title,
    )


def _job_to_detail(job: JobRecord, resolved_title: str | None = None) -> JobDetail:
    return JobDetail(
        id=job.id,
        created_at=job.created_at,
        updated_at=job.updated_at,
        status=job.status,
        source_type=job.source_type,
        source_url=job.source_url,
        title=resolved_title if resolved_title is not None else job.title,
        prefer_youtube_captions=job.prefer_youtube_captions,
        allow_whisper_fallback=True if job.allow_whisper_fallback is None else bool(job.allow_whisper_fallback),
        transcript_source=job.transcript_source,
        captions_attempted=job.captions_attempted,
        captions_status=job.captions_status,
        captions_detail=job.captions_detail,
        summary_object_key=job.summary_object_key,
        transcript_object_key=job.transcript_object_key,
        error=job.error,
    )


def _resolve_job_title_for_response(job: JobRecord, *, allow_oembed: bool) -> tuple[str, str, bool]:
    resolved_title, source = resolve_display_title(job.title, job.source_url, allow_oembed=allow_oembed)
    should_persist = (
        source == "oembed"
        and bool(resolved_title.strip())
        and (not job.title or looks_like_url(job.title) or job.title.strip() != resolved_title)
    )
    return resolved_title, source, should_persist


def _digest_delivery_status() -> tuple[bool, str | None]:
    notifier = Notifier()
    if not notifier.is_configured():
        return False, "SMTP is not configured"
    if not settings.web_app_base_url.strip():
        return False, "WEB_APP_BASE_URL is not configured"
    return True, None


def _build_digest_settings_response(current_user: AuthUser, pref) -> DigestSettingsResponse:
    delivery_available, delivery_reason = _digest_delivery_status()
    return DigestSettingsResponse(
        enabled=bool(pref.enabled) if pref else False,
        cadence=(pref.cadence if pref else "daily"),
        timezone=(pref.timezone_name if pref else "UTC"),
        send_hour_local=int(pref.send_hour_local if pref else settings.digest_send_hour_local),
        weekly_weekday=int(pref.weekly_weekday if pref else settings.digest_weekly_weekday),
        recipient_email=_normalize_owner_email(current_user.email),
        delivery_available=delivery_available,
        delivery_reason=delivery_reason,
        next_send_at=pref.next_send_at if pref else None,
        last_run_at=pref.last_run_at if pref else None,
        last_run_status=pref.last_run_status if pref else None,
        last_sent_at=pref.last_sent_at if pref else None,
        profile_summary=pref.profile_summary if pref else None,
        profile_updated_at=pref.profile_updated_at if pref else None,
        historical_backfill_pending=bool(pref.include_historical_on_next_send) if pref else False,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/api/auth/me", response_model=AuthMeResponse)
def auth_me(current_user: AuthUser = Depends(require_user)) -> AuthMeResponse:
    return AuthMeResponse(email=current_user.email, name=current_user.name, picture=current_user.picture)


@app.get("/api/digests/settings", response_model=DigestSettingsResponse)
def get_digest_settings(
    metadata: MetadataStore = Depends(get_metadata_store),
    current_user: AuthUser = Depends(require_user),
) -> DigestSettingsResponse:
    pref = metadata.get_digest_preference(_normalize_owner_email(current_user.email))
    return _build_digest_settings_response(current_user, pref)


@app.put("/api/digests/settings", response_model=DigestSettingsResponse)
def update_digest_settings(
    payload: UpdateDigestSettingsRequest,
    metadata: MetadataStore = Depends(get_metadata_store),
    current_user: AuthUser = Depends(require_user),
) -> DigestSettingsResponse:
    delivery_available, delivery_reason = _digest_delivery_status()
    if payload.enabled and not delivery_available:
        raise HTTPException(status_code=400, detail=delivery_reason or "Digest delivery is unavailable")

    owner_email = _normalize_owner_email(current_user.email)
    now = datetime.now(timezone.utc)
    pref = metadata.get_digest_preference(owner_email)
    timezone_name = normalize_timezone(payload.timezone)
    cadence = payload.cadence
    display_name = (current_user.name or (pref.display_name if pref else "") or current_user.email).strip() or owner_email
    last_digest_cutoff_at = pref.last_digest_cutoff_at if pref else None
    next_send_at = pref.next_send_at if pref else None
    include_historical_on_next_send = bool(pref.include_historical_on_next_send) if pref else False

    if payload.enabled:
        if not pref:
            last_digest_cutoff_at = None
            include_historical_on_next_send = True
        next_send_at = compute_next_send_at(
            cadence,
            timezone_name,
            now,
            send_hour_local=settings.digest_send_hour_local,
            weekly_weekday=settings.digest_weekly_weekday,
        )
    else:
        next_send_at = None

    saved = metadata.upsert_digest_preference(
        owner_email,
        enabled=payload.enabled,
        cadence=cadence,
        timezone_name=timezone_name,
        send_hour_local=settings.digest_send_hour_local,
        weekly_weekday=settings.digest_weekly_weekday,
        display_name=display_name,
        last_digest_cutoff_at=last_digest_cutoff_at,
        last_run_at=pref.last_run_at if pref else None,
        last_run_status=pref.last_run_status if pref else None,
        last_sent_at=pref.last_sent_at if pref else None,
        next_send_at=next_send_at,
        profile_summary=pref.profile_summary if pref else None,
        profile_updated_at=pref.profile_updated_at if pref else None,
        include_historical_on_next_send=include_historical_on_next_send,
    )
    return _build_digest_settings_response(current_user, saved)


@app.get("/api/digests/history", response_model=list[DigestRunOut])
def get_digest_history(
    metadata: MetadataStore = Depends(get_metadata_store),
    current_user: AuthUser = Depends(require_user),
) -> list[DigestRunOut]:
    rows = metadata.list_digest_runs(_normalize_owner_email(current_user.email), limit=5)
    return [
        DigestRunOut(
            id=row.id,
            status=row.status,
            cadence=row.cadence,
            job_count=row.job_count,
            subject=row.subject,
            window_start_at=row.window_start_at,
            window_end_at=row.window_end_at,
            created_at=row.created_at,
            sent_at=row.sent_at,
        )
        for row in rows
    ]


@app.post("/internal/digests/sweep")
def queue_digest_sweep(
    x_capstone_digest_secret: str | None = Header(default=None, alias="X-Capstone-Digest-Secret"),
) -> dict:
    configured_secret = settings.digest_sweep_secret.strip()
    if not configured_secret:
        raise HTTPException(status_code=503, detail="DIGEST_SWEEP_SECRET is not configured")
    if not x_capstone_digest_secret or x_capstone_digest_secret != configured_secret:
        raise HTTPException(status_code=401, detail="Invalid digest sweep secret")
    rq_job_id = enqueue_digest_sweep()
    return {"queued": True, "job_id": rq_job_id}


@app.post("/api/uploads/presign", response_model=PresignUploadResponse)
def presign_upload(payload: PresignUploadRequest, current_user: AuthUser = Depends(require_user)) -> PresignUploadResponse:
    object_store = get_service_container().object_store
    object_key = build_upload_object_key(payload.filename, current_user.email)
    upload_url = object_store.generate_upload_url(object_key, payload.mime_type, settings.upload_url_expiry_seconds)
    return PresignUploadResponse(
        object_key=object_key,
        upload_url=upload_url,
        headers={"Content-Type": payload.mime_type},
    )


@app.put("/api/uploads/{object_key:path}")
async def upload_local_object(
    object_key: str,
    request: Request,
    current_user: AuthUser = Depends(require_user),
) -> dict:
    """
    Local-object-store upload fallback endpoint.
    For GCS presigned uploads, clients should PUT directly to GCS.
    """
    object_store = get_service_container().object_store
    object_key = sanitize_object_key(object_key)
    _assert_upload_key_owner(object_key, current_user.email)
    data = await request.body()
    content_type = request.headers.get("content-type") or infer_mime_type(object_key)
    object_store.put_bytes(object_key, data, content_type=content_type)
    return {"object_key": object_key, "size_bytes": len(data)}


@app.get("/api/artifacts/{object_key:path}")
def get_local_object(
    object_key: str,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_user),
) -> Response:
    object_store = get_service_container().object_store
    metadata = MetadataStore(db)
    object_key = sanitize_object_key(object_key)
    _assert_object_access(object_key, current_user.email, metadata)
    if not object_store.exists(object_key):
        raise HTTPException(status_code=404, detail="Artifact not found")
    data = object_store.get_bytes(object_key)
    content_type = infer_mime_type(object_key)
    return Response(content=data, media_type=content_type)


@app.post("/api/jobs", response_model=CreateJobResponse)
def create_job(
    payload: CreateJobRequest,
    metadata: MetadataStore = Depends(get_metadata_store),
    current_user: AuthUser = Depends(require_user),
) -> CreateJobResponse:
    has_youtube = bool(payload.youtube_url)
    has_upload = bool(payload.uploaded_object_key)
    if has_youtube == has_upload:
        raise HTTPException(status_code=400, detail="Provide exactly one of youtube_url or uploaded_object_key")

    uploaded_object_key = None
    if payload.uploaded_object_key:
        uploaded_object_key = sanitize_object_key(payload.uploaded_object_key)
        _assert_upload_key_owner(uploaded_object_key, current_user.email)

    source_type = "youtube" if has_youtube else "upload"
    job = metadata.create_job(
        NewJobParams(
            source_type=source_type,
            source_url=payload.youtube_url,
            uploaded_object_key=uploaded_object_key,
            prefer_youtube_captions=payload.prefer_youtube_captions,
            allow_whisper_fallback=payload.allow_whisper_fallback,
        ),
        owner_email=_normalize_owner_email(current_user.email),
    )
    if uploaded_object_key:
        metadata.upsert_artifact(
            job.id,
            "upload",
            uploaded_object_key,
            content_type=infer_mime_type(uploaded_object_key),
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
def list_jobs(
    metadata: MetadataStore = Depends(get_metadata_store),
    current_user: AuthUser = Depends(require_user),
) -> list[JobSummary]:
    jobs = metadata.list_jobs(_normalize_owner_email(current_user.email))
    unresolved_lookup_budget = 8
    summaries: list[JobSummary] = []
    for job in jobs:
        is_unresolved = not job.title or looks_like_url(job.title)
        allow_oembed = bool(is_unresolved and unresolved_lookup_budget > 0)
        if allow_oembed:
            unresolved_lookup_budget -= 1
        resolved_title, source, should_persist = _resolve_job_title_for_response(job, allow_oembed=allow_oembed)
        if should_persist:
            metadata.update_job(job, title=resolved_title)
        logger.debug(
            "job title resolver source=%s persisted=%s job_id=%s",
            source,
            str(bool(should_persist)).lower(),
            job.id,
        )
        summaries.append(_job_to_summary(job, resolved_title=resolved_title))
    return summaries


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_user),
) -> JobDetail:
    metadata = MetadataStore(db)
    job = metadata.get_job(job_id, _normalize_owner_email(current_user.email))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    resolved_title, source, should_persist = _resolve_job_title_for_response(job, allow_oembed=True)
    if should_persist:
        metadata.update_job(job, title=resolved_title)
    logger.debug(
        "job title resolver source=%s persisted=%s job_id=%s",
        source,
        str(bool(should_persist)).lower(),
        job.id,
    )
    return _job_to_detail(job, resolved_title=resolved_title)


def _artifact_response_from_job(job: JobRecord, kind: str, metadata: MetadataStore) -> ArtifactResponse:
    services = get_service_container()
    object_store = services.object_store
    try:
        text, object_key = load_text_artifact(job, kind, metadata, object_store)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    file_link = object_store.generate_download_url(object_key, settings.download_url_expiry_seconds) if object_key else None
    return ArtifactResponse(text=text, object_key=object_key, file_link=file_link)


@app.get("/api/jobs/{job_id}/summary", response_model=ArtifactResponse)
def get_summary(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_user),
) -> ArtifactResponse:
    metadata = MetadataStore(db)
    job = metadata.get_job(job_id, _normalize_owner_email(current_user.email))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _artifact_response_from_job(job, "summary", metadata)


@app.get("/api/jobs/{job_id}/transcript", response_model=ArtifactResponse)
def get_transcript(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_user),
) -> ArtifactResponse:
    metadata = MetadataStore(db)
    job = metadata.get_job(job_id, _normalize_owner_email(current_user.email))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _artifact_response_from_job(job, "transcript", metadata)


@app.get("/api/jobs/{job_id}/chat", response_model=ChatResponse)
def get_job_chat(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_user),
) -> ChatResponse:
    metadata = MetadataStore(db)
    if not metadata.get_job(job_id, _normalize_owner_email(current_user.email)):
        raise HTTPException(status_code=404, detail="Job not found")
    rows = metadata.list_chat(job_id)
    messages = [ChatMessageOut(id=row.id, role=row.role, content=row.content, created_at=row.created_at) for row in rows]
    return ChatResponse(job_id=job_id, messages=messages)


@app.post("/api/jobs/{job_id}/chat", response_model=ChatAnswerResponse)
def post_job_chat(
    job_id: str,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_user),
) -> ChatAnswerResponse:
    metadata = MetadataStore(db)
    services = get_service_container()
    job = metadata.get_job(job_id, _normalize_owner_email(current_user.email))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    transcript = _artifact_response_from_job(job, "transcript", metadata).text
    summary_text = None
    try:
        summary_text = _artifact_response_from_job(job, "summary", metadata).text
    except HTTPException:
        summary_text = None

    history_rows = metadata.list_chat(job_id)
    history_before = [{"role": row.role, "content": row.content} for row in history_rows]

    metadata.append_chat_message(job_id, "user", message)

    try:
        result = services.qa_service.answer_job_chat(
            message,
            transcript_text=transcript,
            conversation_history=history_before,
            summary_text=summary_text,
        )
        metadata.append_chat_message(job_id, "assistant", result.answer)
        return ChatAnswerResponse(answer=result.answer, context_stats=result.contexts)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job chat generation failed for job %s", job_id)
        fallback_answer = (
            "I hit a temporary chat-model error while answering that. "
            "Please send the message again in a few seconds."
        )
        metadata.append_chat_message(job_id, "assistant", fallback_answer)
        return ChatAnswerResponse(
            answer=fallback_answer,
            context_stats=[f"chat_fallback=temporary_model_error:{type(exc).__name__}"],
        )


@app.post("/api/search", response_model=SearchResponse)
def search(
    payload: SearchRequest,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_user),
) -> SearchResponse:
    services = get_service_container()
    object_store = services.object_store
    metadata = MetadataStore(db)
    owner_email = _normalize_owner_email(current_user.email)
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    owner_job_ids = metadata.list_job_ids(owner_email)
    if not owner_job_ids:
        return SearchResponse(answer="No jobs found for this account yet.", hits=[])

    job_ids_filter: set[str] = set(owner_job_ids)
    if payload.created_after or payload.created_before:
        stmt = select(JobRecord.id).where(JobRecord.owner_email == owner_email)
        if payload.created_after:
            stmt = stmt.where(JobRecord.created_at >= payload.created_after)
        if payload.created_before:
            stmt = stmt.where(JobRecord.created_at <= payload.created_before)
        filtered_ids = {str(row[0]) for row in db.execute(stmt).all()}
        job_ids_filter &= filtered_ids
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
