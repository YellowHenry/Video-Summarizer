import logging
from dataclasses import dataclass
from typing import Optional, List, Sequence

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

    def answer_job_chat(
        self,
        question: str,
        transcript_text: str,
        conversation_history: Optional[Sequence[dict]] = None,
        summary_text: Optional[str] = None,
    ) -> QAResult:
        """
        Answer a chat question using full transcript context plus prior chat turns.
        This bypasses vector retrieval by design.
        """
        if not self.summarizer.client:
            return QAResult(answer="OpenAI client is not configured. Set OPENAI_API_KEY.", contexts=[], hits=[])

        cleaned_transcript = (transcript_text or "").strip()
        if not cleaned_transcript:
            return QAResult(answer="Transcript is missing for this job, so chat cannot answer yet.", contexts=[], hits=[])

        history = self._normalize_history(conversation_history or [])
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an assistant for one specific video/audio transcript. "
                    "Use the transcript as source of truth, incorporate prior conversation context, "
                    "and if the transcript does not support a claim, say so directly."
                ),
            },
            {"role": "system", "content": f"Full transcript for this job:\n{cleaned_transcript}"},
        ]
        if summary_text and summary_text.strip():
            messages.append({"role": "system", "content": f"Job summary:\n{summary_text.strip()}"})
        messages.extend(history)
        messages.append({"role": "user", "content": question})

        self.logger.info(
            "Job chat using full transcript context: transcript_chars=%d history_turns=%d",
            len(cleaned_transcript),
            len(history),
        )
        completion = self.summarizer.client.chat.completions.create(
            model=self.summarizer.config.model,
            messages=messages,
            temperature=0.2,
            max_tokens=500,
        )
        answer = completion.choices[0].message.content.strip()
        contexts = [f"full_transcript_chars={len(cleaned_transcript)}", f"history_turns={len(history)}"]
        return QAResult(answer=answer, contexts=contexts, hits=[])

    @staticmethod
    def _normalize_history(history: Sequence[dict]) -> List[dict]:
        normalized: List[dict] = []
        for msg in history:
            role = msg.get("role") if isinstance(msg, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str):
                continue
            cleaned = content.strip()
            if not cleaned:
                continue
            normalized.append({"role": role, "content": cleaned})
        return normalized
