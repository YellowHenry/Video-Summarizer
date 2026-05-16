from __future__ import annotations

from pathlib import Path
from typing import Literal

from .metadata_store import MetadataStore
from .models import JobRecord
from .object_store import BaseObjectStore


def load_text_artifact(
    job: JobRecord,
    kind: Literal["summary", "transcript"],
    metadata: MetadataStore,
    object_store: BaseObjectStore,
) -> tuple[str, str | None]:
    if kind == "summary":
        object_key = job.summary_object_key
        local_filename = "summary.txt"
    elif kind == "transcript":
        object_key = job.transcript_object_key
        local_filename = "transcript.txt"
    else:  # pragma: no cover - defensive
        raise ValueError(f"Unsupported text artifact kind: {kind}")

    if object_key and object_store.exists(object_key):
        text = object_store.get_text(object_key)
        metadata.upsert_artifact(
            job.id,
            kind,
            object_key,
            size_bytes=len(text.encode("utf-8")),
            content_type="text/plain; charset=utf-8",
        )
        return text, object_key

    local_path = Path("storage") / job.id / local_filename
    if local_path.exists():
        text = local_path.read_text(encoding="utf-8")
        fallback_object_key = f"jobs/{job.id}/{local_filename}"
        if not object_store.exists(fallback_object_key):
            object_store.put_text(fallback_object_key, text)
        metadata.upsert_artifact(
            job.id,
            kind,
            fallback_object_key,
            size_bytes=len(text.encode("utf-8")),
            content_type="text/plain; charset=utf-8",
        )
        return text, fallback_object_key

    raise FileNotFoundError(f"{local_filename} not found for job {job.id}")
