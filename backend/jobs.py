import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .compression import CompressionConfig, Compressor
from .downloader import AudioDownloader
from .notifier import Notifier
from .storage import Storage
from .summarizer import CloudSummarizerClient


@dataclass
class Job:
    audio_path: Optional[Path] = None
    youtube_url: Optional[str] = None
    display_name: Optional[str] = None
    prefer_youtube_captions: bool = True
    requester_email: Optional[str] = None
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    summary_path: Optional[Path] = None
    error: Optional[str] = None

    def describe(self) -> str:
        if self.youtube_url:
            return f"YouTube URL {self.youtube_url}"
        if self.audio_path:
            return str(self.audio_path)
        return "unknown source"

    def display_id(self) -> str:
        return self.display_name or self.id


class JobQueue:
    def __init__(self, storage: Storage, summarizer: CloudSummarizerClient, downloader: AudioDownloader, notifier: Optional[Notifier] = None):
        self.storage = storage
        self.summarizer = summarizer
        self.downloader = downloader
        self.notifier = notifier or Notifier()
        self.queue: queue.Queue[Job] = queue.Queue()
        self.listeners: list[Callable[[Job], None]] = []
        self.logger = logging.getLogger(__name__)
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def add_listener(self, callback: Callable[[Job], None]) -> None:
        self.listeners.append(callback)

    def submit(self, job: Job) -> Job:
        self.queue.put(job)
        self._publish(job)
        return job

    def _publish(self, job: Job) -> None:
        for listener in self.listeners:
            listener(job)

    def _worker(self) -> None:
        while True:
            job: Job = self.queue.get()
            try:
                self._run_job(job)
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.error = str(exc)
                self.logger.exception("Job %s failed", job.id)
                self._publish(job)
            finally:
                self.queue.task_done()

    def _run_job(self, job: Job) -> None:
        job.status = "downloading"
        self._publish(job)
        if job.youtube_url:
            local_copy, title = self.downloader.download_youtube(job.youtube_url)
            job.display_name = title or job.display_name
        elif job.audio_path:
            local_copy = self.downloader.copy_local(job.audio_path)
        else:
            raise ValueError("Job missing both audio_path and youtube_url")

        job.status = "preprocessing"
        self._publish(job)
        compressed = Compressor(job.compression).compress(local_copy)
        job.status = "summarizing"
        self._publish(job)
        stored_path = self.storage.store_compressed_copy(job.id, compressed)
        summary = self.summarizer.summarize(
            stored_path,
            youtube_url=job.youtube_url,
            prefer_youtube_captions=job.prefer_youtube_captions,
        )
        job.summary_path = self.storage.store_summary(job.id, summary)

        if job.requester_email:
            subject = f"Your audio summary for job {job.id} is ready"
            body = summary if len(summary) < 2000 else summary[:2000] + "..."
            self.notifier.notify(job.requester_email, subject, body)

        job.status = "complete"
        self._publish(job)
