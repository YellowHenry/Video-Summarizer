import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .compression import CompressionConfig, Compressor
from .downloader import VideoDownloader
from .notifier import Notifier
from .storage import Storage
from .summarizer import CloudSummarizerClient


@dataclass
class Job:
    video_path: Optional[Path] = None
    youtube_url: Optional[str] = None
    requester_email: Optional[str] = None
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    summary_path: Optional[Path] = None
    error: Optional[str] = None

    def describe(self) -> str:
        if self.youtube_url:
            return f"YouTube URL {self.youtube_url}"
        if self.video_path:
            return str(self.video_path)
        return "unknown source"


class JobQueue:
    def __init__(self, storage: Storage, summarizer: CloudSummarizerClient, downloader: VideoDownloader, notifier: Optional[Notifier] = None):
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
            local_copy = self.downloader.download_youtube(job.youtube_url)
        elif job.video_path:
            local_copy = self.downloader.copy_local(job.video_path)
        else:
            raise ValueError("Job missing both video_path and youtube_url")

        job.status = "compressing"
        self._publish(job)
        compressed = Compressor(job.compression).compress(local_copy)
        self.storage.store_compressed_copy(job.id, compressed)

        job.status = "summarizing"
        self._publish(job)
        summary = self.summarizer.summarize(compressed)
        job.summary_path = self.storage.store_summary(job.id, summary)

        if job.requester_email:
            subject = f"Your video summary for job {job.id} is ready"
            body = summary if len(summary) < 2000 else summary[:2000] + "..."
            self.notifier.notify(job.requester_email, subject, body)

        job.status = "complete"
        self._publish(job)
