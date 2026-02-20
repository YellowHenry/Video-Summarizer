import io
import logging
import mimetypes
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from .config import settings

try:
    from google.cloud import storage as gcs_storage
except ImportError:  # pragma: no cover - optional dependency
    gcs_storage = None


INVALID_KEY_PATTERN = re.compile(r"(^/)|(\.\.)|(^[A-Za-z]:)")
logger = logging.getLogger(__name__)


def sanitize_object_key(key: str) -> str:
    normalized = key.replace("\\", "/").strip()
    normalized = re.sub(r"/+", "/", normalized)
    if INVALID_KEY_PATTERN.search(normalized):
        raise ValueError("Invalid object key")
    return normalized.lstrip("/")


def build_upload_object_key(filename: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)
    return f"uploads/{uuid.uuid4().hex}_{safe_name}"


class BaseObjectStore:
    def generate_upload_url(self, object_key: str, mime_type: str, expires_seconds: int) -> str:
        raise NotImplementedError

    def generate_download_url(self, object_key: str, expires_seconds: int) -> Optional[str]:
        raise NotImplementedError

    def put_bytes(self, object_key: str, data: bytes, content_type: Optional[str] = None) -> None:
        raise NotImplementedError

    def put_text(self, object_key: str, text: str, encoding: str = "utf-8") -> None:
        self.put_bytes(object_key, text.encode(encoding), content_type="text/plain; charset=utf-8")

    def get_bytes(self, object_key: str) -> bytes:
        raise NotImplementedError

    def get_text(self, object_key: str, encoding: str = "utf-8") -> str:
        return self.get_bytes(object_key).decode(encoding)

    def exists(self, object_key: str) -> bool:
        raise NotImplementedError

    def download_to_temp(self, object_key: str, suffix: str = "") -> Path:
        key = sanitize_object_key(object_key)
        data = self.get_bytes(key)
        tmp_dir = Path(tempfile.mkdtemp(prefix="obj_"))
        tmp_path = tmp_dir / (Path(key).name + suffix)
        tmp_path.write_bytes(data)
        return tmp_path


class LocalObjectStore(BaseObjectStore):
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, object_key: str, create_parents: bool = False) -> Path:
        key = sanitize_object_key(object_key)
        path = self.root / key
        if create_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def generate_upload_url(self, object_key: str, mime_type: str, expires_seconds: int) -> str:
        key = sanitize_object_key(object_key)
        return f"{settings.api_base_url.rstrip('/')}/api/uploads/{key}"

    def generate_download_url(self, object_key: str, expires_seconds: int) -> Optional[str]:
        key = sanitize_object_key(object_key)
        return f"{settings.api_base_url.rstrip('/')}/api/artifacts/{key}"

    def put_bytes(self, object_key: str, data: bytes, content_type: Optional[str] = None) -> None:
        self._path(object_key, create_parents=True).write_bytes(data)

    def get_bytes(self, object_key: str) -> bytes:
        return self._path(object_key, create_parents=False).read_bytes()

    def exists(self, object_key: str) -> bool:
        return self._path(object_key, create_parents=False).exists()


class GCSObjectStore(BaseObjectStore):
    def __init__(self, bucket_name: str):
        if not gcs_storage:
            raise RuntimeError("google-cloud-storage is required for OBJECT_STORAGE_BACKEND=gcs")
        self.client = gcs_storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def _blob(self, object_key: str):
        key = sanitize_object_key(object_key)
        return self.bucket.blob(key)

    def generate_upload_url(self, object_key: str, mime_type: str, expires_seconds: int) -> str:
        key = sanitize_object_key(object_key)
        blob = self.bucket.blob(key)
        try:
            return blob.generate_signed_url(
                version="v4",
                expiration=expires_seconds,
                method="PUT",
                content_type=mime_type,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Falling back to API upload URL for %s; signed URL generation failed: %s",
                key,
                exc,
            )
            return f"{settings.api_base_url.rstrip('/')}/api/uploads/{key}"

    def generate_download_url(self, object_key: str, expires_seconds: int) -> Optional[str]:
        key = sanitize_object_key(object_key)
        blob = self.bucket.blob(key)
        try:
            return blob.generate_signed_url(version="v4", expiration=expires_seconds, method="GET")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Falling back to API artifact URL for %s; signed URL generation failed: %s",
                key,
                exc,
            )
            return f"{settings.api_base_url.rstrip('/')}/api/artifacts/{key}"

    def put_bytes(self, object_key: str, data: bytes, content_type: Optional[str] = None) -> None:
        blob = self._blob(object_key)
        blob.upload_from_file(io.BytesIO(data), size=len(data), content_type=content_type)

    def get_bytes(self, object_key: str) -> bytes:
        return self._blob(object_key).download_as_bytes()

    def exists(self, object_key: str) -> bool:
        return self._blob(object_key).exists()


def get_object_store() -> BaseObjectStore:
    if settings.object_backend == "gcs":
        if not settings.gcs_bucket:
            raise RuntimeError("GCS_BUCKET must be set for OBJECT_STORAGE_BACKEND=gcs")
        return GCSObjectStore(settings.gcs_bucket)
    return LocalObjectStore(settings.local_object_root)


def infer_mime_type(filename: str) -> str:
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"
