from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PresignUploadRequest(BaseModel):
    filename: str
    mime_type: str = "application/octet-stream"


class PresignUploadResponse(BaseModel):
    object_key: str
    upload_url: str
    method: str = "PUT"
    headers: dict = Field(default_factory=dict)


class AuthMeResponse(BaseModel):
    email: str
    name: Optional[str] = None
    picture: Optional[str] = None


class CreateJobRequest(BaseModel):
    youtube_url: Optional[str] = None
    uploaded_object_key: Optional[str] = None
    prefer_youtube_captions: bool = True
    allow_whisper_fallback: bool = True


class CreateJobResponse(BaseModel):
    job_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class JobSummary(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    status: str
    source_type: str
    source_url: Optional[str] = None
    title: Optional[str] = None


class JobDetail(JobSummary):
    prefer_youtube_captions: bool
    allow_whisper_fallback: bool
    transcript_source: Optional[str] = None
    captions_attempted: Optional[bool] = None
    captions_status: Optional[str] = None
    captions_detail: Optional[str] = None
    summary_object_key: Optional[str] = None
    transcript_object_key: Optional[str] = None
    error: Optional[str] = None


class ArtifactResponse(BaseModel):
    text: str
    object_key: Optional[str] = None
    file_link: Optional[str] = None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


class ChatResponse(BaseModel):
    job_id: str
    messages: list[ChatMessageOut]


class ChatRequest(BaseModel):
    message: str


class ChatAnswerResponse(BaseModel):
    answer: str
    context_stats: list[str]


class SearchRequest(BaseModel):
    question: str
    youtube_url: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=25)
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None


class SearchHit(BaseModel):
    job_id: str
    kind: str
    chunk_index: int
    file_path: Optional[str] = None
    object_key: Optional[str] = None
    file_link: Optional[str] = None
    snippet: str


class SearchResponse(BaseModel):
    answer: str
    hits: list[SearchHit]


class DigestSettingsResponse(BaseModel):
    enabled: bool
    cadence: Literal["daily", "weekly"]
    timezone: str
    send_hour_local: int
    weekly_weekday: int
    recipient_email: str
    delivery_available: bool
    delivery_reason: Optional[str] = None
    next_send_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    last_sent_at: Optional[datetime] = None
    profile_summary: Optional[str] = None
    profile_updated_at: Optional[datetime] = None
    historical_backfill_pending: bool = False


class UpdateDigestSettingsRequest(BaseModel):
    enabled: bool
    cadence: Literal["daily", "weekly"] = "daily"
    timezone: str = "UTC"


class DigestRunOut(BaseModel):
    id: int
    status: str
    cadence: str
    job_count: int
    subject: Optional[str] = None
    window_start_at: Optional[datetime] = None
    window_end_at: Optional[datetime] = None
    created_at: datetime
    sent_at: Optional[datetime] = None
