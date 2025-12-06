"""Quick smoke test to ensure the pipeline functions end-to-end without the UI."""
from pathlib import Path
import time
import wave

from backend.jobs import JobQueue, Job
from backend.storage import Storage
from backend.summarizer import CloudSummarizerClient, SummarizerConfig
from backend.downloader import VideoDownloader


def _build_input() -> Path:
    """Generate a dummy media file that exercises configured providers."""

    config = SummarizerConfig()
    if config.api_key or config.endpoint:
        # Create a short silent WAV for Whisper/HTTP uploads
        dummy = Path("smoke_input.wav")
        with wave.open(dummy, "w") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x00\x00" * 16000)
    else:
        dummy = Path("smoke_input.mp4")
        dummy.write_text("synthetic video bytes", encoding="utf-8")
    return dummy


def run_smoke_test(timeout: float = 10.0) -> Path:
    dummy = _build_input()

    queue = JobQueue(Storage(), CloudSummarizerClient(), VideoDownloader())
    job = Job(video_path=dummy)
    queue.submit(job)

    waited = 0.0
    while waited < timeout and job.status not in {"complete", "failed"}:
        time.sleep(0.2)
        waited += 0.2

    if job.status != "complete" or not job.summary_path:
        raise RuntimeError(f"Smoke test did not finish successfully (status={job.status})")
    return job.summary_path


if __name__ == "__main__":
    summary_path = run_smoke_test()
    print(f"Smoke test succeeded, summary written to: {summary_path}")
    print(summary_path.read_text(encoding="utf-8"))
