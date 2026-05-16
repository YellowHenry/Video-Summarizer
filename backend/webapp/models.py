from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    owner_email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="queued")
    source_type: Mapped[str] = mapped_column(String(16), default="upload")  # upload|youtube
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)

    prefer_youtube_captions: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_whisper_fallback: Mapped[bool] = mapped_column(Boolean, default=True)
    transcript_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    captions_attempted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    captions_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    captions_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    artifacts: Mapped[list["JobArtifact"]] = relationship(
        "JobArtifact",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    vectors: Mapped[list["VectorChunk"]] = relationship(
        "VectorChunk",
        back_populates="job",
        cascade="all, delete-orphan",
    )


class DigestPreference(Base):
    __tablename__ = "digest_preferences"

    owner_email: Mapped[str] = mapped_column(String(320), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cadence: Mapped[str] = mapped_column(String(16), default="daily", nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    send_hour_local: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    weekly_weekday: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(320), nullable=True)
    last_digest_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    include_historical_on_next_send: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    profile_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, index=True)


class DigestRun(Base):
    __tablename__ = "digest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    window_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    job_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    included_job_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_summary_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    job: Mapped[JobRecord] = relationship("JobRecord", back_populates="chat_messages")


class JobArtifact(Base):
    __tablename__ = "job_artifacts"
    __table_args__ = (UniqueConstraint("job_id", "kind", name="uq_job_artifact_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # summary|transcript|upload|media|other
    object_key: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    job: Mapped[JobRecord] = relationship("JobRecord", back_populates="artifacts")


class VectorChunk(Base):
    __tablename__ = "vectors"
    __table_args__ = (UniqueConstraint("job_id", "kind", "chunk_index", name="uq_vectors_job_kind_chunk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    job: Mapped[JobRecord] = relationship("JobRecord", back_populates="vectors")
