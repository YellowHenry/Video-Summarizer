import logging
from dataclasses import dataclass
from typing import Optional, List

from .summarizer import CloudSummarizerClient, SummarizerConfig
from .vector_store import VectorStore


@dataclass
class QAResult:
    answer: str
    contexts: List[str]
    hits: List["VectorRecord"]


class QAService:
    def __init__(self, vector_store: Optional[VectorStore] = None, summarizer_client: Optional[CloudSummarizerClient] = None):
        self.vector_store = vector_store or VectorStore()
        self.summarizer = summarizer_client or CloudSummarizerClient(SummarizerConfig())
        self.logger = logging.getLogger(__name__)

    def answer(self, question: str, youtube_url: Optional[str] = None, job_id: Optional[str] = None, top_k: int = 5) -> QAResult:
        records = self.vector_store.query(question, top_k=top_k, job_id=job_id, source_url=youtube_url)
        if not records:
            return QAResult(answer="No indexed transcripts available to answer this question.", contexts=[], hits=[])

        context_blocks = []
        for rec in records:
            link = rec.file_path or ""
            context_blocks.append(f"[job {rec.job_id} {rec.kind} chunk {rec.chunk_index}] {rec.text}\n{link}")
        context_text = "\n\n".join(context_blocks)

        prompt = (
            "You are answering user questions using prior transcripts. "
            "Cite relevant details concisely. If unsure, say you don't know.\n\n"
            f"Context:\n{context_text}\n\nQuestion: {question}\nAnswer:"
        )
        if not self.summarizer.client:
            return QAResult(answer="OpenAI client is not configured. Set OPENAI_API_KEY.", contexts=context_blocks)

        completion = self.summarizer.client.chat.completions.create(
            model=self.summarizer.config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400,
        )
        answer = completion.choices[0].message.content.strip()
        return QAResult(answer=answer, contexts=context_blocks, hits=records)
