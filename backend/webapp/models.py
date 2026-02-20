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

    status: Mapped[str] = mapped_column(String(32), default="queued")
    source_type: Mapped[str] = mapped_column(String(16), default="upload")  # upload|youtube
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)

    prefer_youtube_captions: Mapped[bool] = mapped_column(Boolean, default=True)
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
