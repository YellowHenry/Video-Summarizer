from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PresignUploadRequest(BaseModel):
    filename: str
    mime_type: str = "application/octet-stream"


class PresignUploadResponse(BaseModel):
    object_key: str
    upload_url: str
    method: str = "PUT"
    headers: dict = Field(default_factory=dict)


class CreateJobRequest(BaseModel):
    youtube_url: Optional[str] = None
    uploaded_object_key: Optional[str] = None
    prefer_youtube_captions: bool = True


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
