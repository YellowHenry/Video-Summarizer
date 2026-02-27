from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Optional, Set

from sqlalchemy import delete, or_, select, text
from sqlalchemy.orm import Session

from backend.vector_store import VectorRecord

from .models import ChatMessage, JobArtifact, JobRecord, VectorChunk


@dataclass
class NewJobParams:
    source_type: str
    source_url: Optional[str]
    uploaded_object_key: Optional[str]
    prefer_youtube_captions: bool
    allow_whisper_fallback: bool


class MetadataStore:
    """
    Thin metadata adapter over SQLAlchemy models used by API and worker paths.
    """

    def __init__(self, session: Session):
        self.session = session
        self.logger = logging.getLogger(__name__)
        self._has_pgvector_column_cache: Optional[bool] = None

    def create_job(self, params: NewJobParams, owner_email: str) -> JobRecord:
        job = JobRecord(
            owner_email=owner_email.strip().lower(),
            source_type=params.source_type,
            source_url=params.source_url,
            uploaded_object_key=params.uploaded_object_key,
            prefer_youtube_captions=params.prefer_youtube_captions,
            allow_whisper_fallback=params.allow_whisper_fallback,
            status="queued",
        )
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def get_job(self, job_id: str, owner_email: str | None = None) -> JobRecord | None:
        if owner_email is None:
            return self.session.get(JobRecord, job_id)
        return (
            self.session.execute(
                select(JobRecord).where(
                    JobRecord.id == job_id,
                    JobRecord.owner_email == owner_email.strip().lower(),
                )
            )
            .scalars()
            .first()
        )

    def list_jobs(self, owner_email: str | None = None) -> list[JobRecord]:
        stmt = select(JobRecord)
        if owner_email is not None:
            stmt = stmt.where(JobRecord.owner_email == owner_email.strip().lower())
        stmt = stmt.order_by(JobRecord.created_at.desc())
        return self.session.execute(stmt).scalars().all()

    def list_job_ids(self, owner_email: str) -> set[str]:
        rows = self.session.execute(
            select(JobRecord.id).where(JobRecord.owner_email == owner_email.strip().lower())
        ).all()
        return {str(row[0]) for row in rows}

    def update_job(self, job: JobRecord, **fields) -> JobRecord:
        for key, value in fields.items():
            setattr(job, key, value)
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def list_chat(self, job_id: str) -> list[ChatMessage]:
        return (
            self.session.execute(
                select(ChatMessage)
                .where(ChatMessage.job_id == job_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            .scalars()
            .all()
        )

    def append_chat_message(self, job_id: str, role: str, content: str) -> ChatMessage:
        row = ChatMessage(job_id=job_id, role=role, content=content)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def replace_chat(self, job_id: str, messages: list[dict]) -> None:
        self.session.query(ChatMessage).filter(ChatMessage.job_id == job_id).delete()
        for item in messages:
            role = item.get("role")
            content = item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                self.session.add(ChatMessage(job_id=job_id, role=role, content=content.strip()))
        self.session.commit()

    def upsert_artifact(
        self,
        job_id: str,
        kind: str,
        object_key: str,
        *,
        size_bytes: int | None = None,
        content_type: str | None = None,
    ) -> JobArtifact:
        row = (
            self.session.execute(
                select(JobArtifact).where(JobArtifact.job_id == job_id, JobArtifact.kind == kind)
            )
            .scalars()
            .first()
        )
        if not row:
            row = JobArtifact(job_id=job_id, kind=kind, object_key=object_key)
        row.object_key = object_key
        row.size_bytes = size_bytes
        row.content_type = content_type
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def replace_vectors(self, job_id: str, records: list[VectorRecord], model: str = "text-embedding-3-large") -> int:
        self.session.execute(delete(VectorChunk).where(VectorChunk.job_id == job_id))
        count = 0
        for record in records:
            row = VectorChunk(
                job_id=record.job_id,
                kind=record.kind,
                chunk_index=record.chunk_index,
                text=record.text,
                source_url=record.source_url,
                file_path=record.file_path,
                embedding_json=json.dumps(record.embedding),
                embedding_model=model,
            )
            self.session.add(row)
            count += 1
        self.session.commit()
        self._sync_pgvector_column(job_id)
        return count

    def query_vectors_by_embedding(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        job_ids: Optional[Set[str]] = None,
        source_url: Optional[str] = None,
    ) -> list[VectorRecord]:
        pgvector_hits = self._query_vectors_pgvector(
            query_embedding,
            top_k=top_k,
            job_ids=job_ids,
            source_url=source_url,
        )
        if pgvector_hits is not None:
            return pgvector_hits

        stmt = select(VectorChunk)
        if job_ids is not None:
            if not job_ids:
                return []
            stmt = stmt.where(VectorChunk.job_id.in_(job_ids))
        if source_url:
            stmt = stmt.where(VectorChunk.source_url == source_url)

        rows = self.session.execute(stmt).scalars().all()
        scored: list[tuple[float, VectorRecord]] = []
        for row in rows:
            if not row.embedding_json:
                continue
            try:
                embedding = json.loads(row.embedding_json)
            except Exception:
                continue
            if not isinstance(embedding, list) or not embedding:
                continue
            score = self._cosine_similarity(query_embedding, embedding)
            scored.append(
                (
                    score,
                    VectorRecord(
                        text=row.text,
                        job_id=row.job_id,
                        source_url=row.source_url,
                        chunk_index=row.chunk_index,
                        kind=row.kind,
                        file_path=row.file_path,
                        embedding=embedding,
                    ),
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored[:top_k]]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _sync_pgvector_column(self, job_id: str) -> None:
        if not self._supports_pgvector_column():
            return
        try:
            self.session.execute(
                text(
                    """
                    UPDATE vectors
                    SET embedding_vector = embedding_json::vector
                    WHERE job_id = :job_id
                      AND embedding_json IS NOT NULL
                      AND embedding_json <> ''
                    """
                ),
                {"job_id": job_id},
            )
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Failed to sync pgvector embeddings for job %s: %s", job_id, exc)

    def _supports_pgvector_column(self) -> bool:
        if self._has_pgvector_column_cache is not None:
            return self._has_pgvector_column_cache
        bind = self.session.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            self._has_pgvector_column_cache = False
            return False
        try:
            found = self.session.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'vectors'
                      AND column_name = 'embedding_vector'
                    LIMIT 1
                    """
                )
            ).first()
            self._has_pgvector_column_cache = bool(found)
        except Exception:  # noqa: BLE001
            self._has_pgvector_column_cache = False
        return self._has_pgvector_column_cache

    def _query_vectors_pgvector(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        job_ids: Optional[Set[str]],
        source_url: Optional[str],
    ) -> list[VectorRecord] | None:
        if not query_embedding:
            return []
        if not self._supports_pgvector_column():
            return None

        params: dict[str, object] = {
            "query_vector": self._vector_literal(query_embedding),
            "top_k": int(top_k),
        }
        where_clauses = ["embedding_vector IS NOT NULL"]

        if job_ids is not None:
            if not job_ids:
                return []
            placeholders: list[str] = []
            for index, job_id in enumerate(sorted(job_ids)):
                key = f"job_id_{index}"
                placeholders.append(f":{key}")
                params[key] = job_id
            where_clauses.append(f"job_id IN ({', '.join(placeholders)})")

        if source_url:
            where_clauses.append("source_url = :source_url")
            params["source_url"] = source_url

        sql = f"""
            SELECT job_id, kind, chunk_index, text, source_url, file_path, embedding_json
            FROM vectors
            WHERE {' AND '.join(where_clauses)}
            ORDER BY embedding_vector <=> CAST(:query_vector AS vector)
            LIMIT :top_k
        """
        try:
            rows = self.session.execute(text(sql), params).mappings().all()
            hits: list[VectorRecord] = []
            for row in rows:
                embedding_json = row.get("embedding_json")
                embedding: list[float] = []
                if isinstance(embedding_json, str) and embedding_json:
                    try:
                        parsed = json.loads(embedding_json)
                        if isinstance(parsed, list):
                            embedding = parsed
                    except Exception:
                        embedding = []
                hits.append(
                    VectorRecord(
                        text=str(row.get("text") or ""),
                        job_id=str(row.get("job_id")),
                        source_url=row.get("source_url"),
                        chunk_index=int(row.get("chunk_index") or 0),
                        kind=str(row.get("kind") or "transcript"),
                        file_path=row.get("file_path"),
                        embedding=embedding,
                    )
                )
            return hits
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("pgvector query failed, falling back to cosine in app: %s", exc)
            return None

    @staticmethod
    def _vector_literal(values: list[float]) -> str:
        # pgvector text input format: [1,2,3]
        return "[" + ",".join(f"{value:.8f}" for value in values) + "]"

    def has_object_access(self, object_key: str, owner_email: str) -> bool:
        normalized_owner = owner_email.strip().lower()
        has_job_object = (
            self.session.execute(
                select(JobRecord.id).where(
                    JobRecord.owner_email == normalized_owner,
                    or_(
                        JobRecord.summary_object_key == object_key,
                        JobRecord.transcript_object_key == object_key,
                        JobRecord.uploaded_object_key == object_key,
                    ),
                )
            )
            .first()
            is not None
        )
        if has_job_object:
            return True

        has_artifact = (
            self.session.execute(
                select(JobArtifact.id)
                .join(JobRecord, JobArtifact.job_id == JobRecord.id)
                .where(
                    JobArtifact.object_key == object_key,
                    JobRecord.owner_email == normalized_owner,
                )
            )
            .first()
            is not None
        )
        return has_artifact
