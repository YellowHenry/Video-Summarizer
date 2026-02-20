import json
import math
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .config import get_openai_api_key

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


@dataclass
class VectorRecord:
    text: str
    job_id: str
    source_url: Optional[str]
    chunk_index: int
    kind: str  # "transcript" or "summary"
    file_path: Optional[str]
    embedding: List[float]


class VectorStore:
    def __init__(self, base_dir: Path = Path("storage") / "index"):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.json"
        self.records: List[VectorRecord] = []
        self._load()
        api_key = get_openai_api_key()
        self.client = (
            OpenAI(api_key=api_key)
            if OpenAI and api_key
            else None
        )
        self.logger = logging.getLogger(__name__)

    def _load(self) -> None:
        if self.index_path.exists():
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            self.records = [
                VectorRecord(
                    text=item["text"],
                    job_id=item["job_id"],
                    source_url=item.get("source_url"),
                    chunk_index=item["chunk_index"],
                    kind=item.get("kind", "transcript"),
                    file_path=item.get("file_path"),
                    embedding=item["embedding"],
                )
                for item in data
            ]

    def _save(self) -> None:
        payload = [
            {
                "text": r.text,
                "job_id": r.job_id,
                "source_url": r.source_url,
                "chunk_index": r.chunk_index,
                "kind": r.kind,
                "file_path": r.file_path,
                "embedding": r.embedding,
            }
            for r in self.records
        ]
        self.index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _embed(self, texts: List[str]) -> List[List[float]]:
        if not self.client:
            raise RuntimeError("OpenAI client not configured for embeddings")
        response = self.client.embeddings.create(model="text-embedding-3-large", input=texts)
        return [item.embedding for item in response.data]

    def _chunk(self, text: str, max_chars: int = 1200) -> List[Tuple[int, str]]:
        chunks: List[Tuple[int, str]] = []
        start = 0
        idx = 0
        length = len(text)
        while start < length:
            end = min(length, start + max_chars)
            chunks.append((idx, text[start:end]))
            idx += 1
            start = end
        return chunks

    def add_text(self, job_id: str, text: str, source_url: Optional[str], kind: str, file_path: Optional[Path] = None) -> None:
        if not text.strip() or self._is_placeholder_text(text):
            return
        chunks = self._chunk(text)
        self.logger.info("Embedding %s chunks for job %s kind=%s file=%s", len(chunks), job_id, kind, file_path or "memory")
        embeddings = self._embed([chunk for _, chunk in chunks])
        for (chunk_idx, chunk_text), emb in zip(chunks, embeddings):
            self.records.append(
                VectorRecord(
                    text=chunk_text,
                    job_id=job_id,
                    source_url=source_url,
                    chunk_index=chunk_idx,
                    kind=kind,
                    file_path=str(file_path) if file_path else None,
                    embedding=emb,
                )
            )
        self._save()
        self.logger.info("Indexed %s chunks for job %s kind=%s", len(chunks), job_id, kind)

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        job_id: Optional[str] = None,
        source_url: Optional[str] = None,
        job_ids: Optional[Set[str]] = None,
    ) -> List[VectorRecord]:
        if not self.records:
            return []
        query_vec = self._embed([query_text])[0]
        scored: List[Tuple[float, VectorRecord]] = []
        for record in self.records:
            if job_id and record.job_id != job_id:
                continue
            if job_ids is not None and record.job_id not in job_ids:
                continue
            if source_url and record.source_url != source_url:
                continue
            score = self._cosine_similarity(query_vec, record.embedding)
            scored.append((score, record))
        scored.sort(key=lambda x: x[0], reverse=True)
        self.logger.info("Query '%s' returning %s of %s candidates", query_text, min(top_k, len(scored)), len(scored))
        return [rec for _, rec in scored[:top_k]]

    def remove_job_records(self, job_id: str) -> int:
        """Remove all indexed chunks for a specific job id."""
        before = len(self.records)
        self.records = [record for record in self.records if record.job_id != job_id]
        removed = before - len(self.records)
        if removed:
            self._save()
            self.logger.info("Removed %s vector chunks for job %s", removed, job_id)
        return removed

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def populate_from_storage(self, storage_root: Path = Path("storage")) -> None:
        """Scan existing storage/<job_id> for transcript.txt and summary.txt and index any not yet recorded."""
        existing_keys: Dict[Tuple[str, str, int], bool] = {
            (r.job_id, r.kind, r.chunk_index) for r in self.records
        }
        total_new = 0
        for job_dir in storage_root.iterdir() if storage_root.exists() else []:
            if not job_dir.is_dir():
                continue
            job_id = job_dir.name
            for kind, filename in (("transcript", "transcript.txt"), ("summary", "summary.txt")):
                path = job_dir / filename
                if not path.exists():
                    continue
                if self._is_placeholder(path):
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    continue
                text = path.read_text(encoding="utf-8")
                chunks = self._chunk(text)
                needed_chunks = []
                for chunk_idx, chunk_text in chunks:
                    key = (job_id, kind, chunk_idx)
                    if key not in existing_keys:
                        needed_chunks.append((chunk_idx, chunk_text, key))
                if not needed_chunks:
                    continue
                self.logger.info("Indexing existing %s for job %s from %s (%s chunks)", kind, job_id, path, len(needed_chunks))
                embeddings = self._embed([t for _, t, _ in needed_chunks])
                for (chunk_idx, chunk_text, key), emb in zip(needed_chunks, embeddings):
                    self.records.append(
                        VectorRecord(
                            text=chunk_text,
                            job_id=job_id,
                            source_url=None,
                            chunk_index=chunk_idx,
                            kind=kind,
                            file_path=str(path),
                            embedding=emb,
                        )
                    )
                    existing_keys.add(key)
                    total_new += 1
        self._save()
        if total_new:
            self.logger.info("Populate from storage complete. Added %s new chunks.", total_new)
        else:
            self.logger.info("Populate from storage complete. No new chunks added.")

    @staticmethod
    def _is_placeholder(path: Path) -> bool:
        try:
            content = path.read_text(encoding="utf-8").strip().lower()
        except Exception:
            return False
        prefix = "it seems that the transcript you intended to provide is missing"
        return content.startswith(prefix)

    @staticmethod
    def _is_placeholder_text(text: str) -> bool:
        return text.strip().lower().startswith("it seems that the transcript you intended to provide is missing")
