from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from backend.qa import QAService
from backend.summarizer import CloudSummarizerClient
from backend.vector_store import VectorStore

from .object_store import BaseObjectStore, get_object_store


@dataclass
class ServiceContainer:
    object_store: BaseObjectStore
    vector_store: VectorStore
    summarizer_client: CloudSummarizerClient
    qa_service: QAService


@lru_cache(maxsize=1)
def get_services() -> ServiceContainer:
    object_store = get_object_store()
    vector_store = VectorStore()
    summarizer_client = CloudSummarizerClient()
    qa_service = QAService(vector_store=vector_store, summarizer_client=summarizer_client)
    return ServiceContainer(
        object_store=object_store,
        vector_store=vector_store,
        summarizer_client=summarizer_client,
        qa_service=qa_service,
    )
