import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class StorageConfig:
    base_dir: Path = Path("storage")


class Storage:
    def __init__(self, config: Optional[StorageConfig] = None):
        self.config = config or StorageConfig()
        self.config.base_dir.mkdir(parents=True, exist_ok=True)

    def store_metadata(self, job) -> Path:
        """
        Persist lightweight metadata for a job so we can restore created_at,
        title, source, and status when the app restarts.
        """
        summary_dir = self.config.base_dir / job.id
        summary_dir.mkdir(parents=True, exist_ok=True)
        meta_path = summary_dir / "meta.json"
        payload = {
            "job_id": job.id,
            "created_at": getattr(job, "created_at", None),
            "youtube_url": getattr(job, "youtube_url", None),
            "audio_path": str(getattr(job, "audio_path", None)) if getattr(job, "audio_path", None) else None,
            "title": getattr(job, "title", None),
            "display_name": getattr(job, "display_name", None),
            "status": getattr(job, "status", None),
            "prefer_youtube_captions": getattr(job, "prefer_youtube_captions", None),
            "allow_whisper_fallback": getattr(job, "allow_whisper_fallback", None),
            "transcript_source": getattr(job, "transcript_source", None),
            "captions_attempted": getattr(job, "captions_attempted", None),
            "captions_status": getattr(job, "captions_status", None),
            "captions_detail": getattr(job, "captions_detail", None),
        }
        meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return meta_path

    def store_summary(self, job_id: str, summary_text: str) -> Path:
        summary_dir = self.config.base_dir / job_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        json_path = summary_dir / "summary.json"
        payload = {"job_id": job_id, "summary": summary_text}
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        text_path = summary_dir / "summary.txt"
        text_path.write_text(summary_text, encoding="utf-8")
        return text_path

    def store_title(self, job_id: str, title: str) -> Path:
        summary_dir = self.config.base_dir / job_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        path = summary_dir / "title.txt"
        path.write_text(title, encoding="utf-8")
        return path

    def store_transcript(self, job_id: str, transcript_text: str) -> Path:
        """Persist the raw transcript text for a job."""
        summary_dir = self.config.base_dir / job_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = summary_dir / "transcript.txt"
        transcript_path.write_text(transcript_text, encoding="utf-8")
        return transcript_path

    def store_compressed_copy(self, job_id: str, media: Path) -> Path:
        summary_dir = self.config.base_dir / job_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        output_path = summary_dir / media.name
        if media.resolve() != output_path.resolve():
            output_path.write_bytes(media.read_bytes())
        return output_path

    def delete_summary_and_transcript(self, job_id: str) -> None:
        """Remove summary/transcript artifacts for a job (used when summaries are invalid)."""
        summary_dir = self.config.base_dir / job_id
        for filename in ("summary.txt", "summary.json", "transcript.txt"):
            path = summary_dir / filename
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass

    def append_chat(self, job_id: str, role: str, content: str) -> None:
        summary_dir = self.config.base_dir / job_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        chat_path = summary_dir / "chat.json"
        chat = []
        if chat_path.exists():
            try:
                chat = json.loads(chat_path.read_text(encoding="utf-8"))
            except Exception:
                chat = []
        chat.append({"role": role, "content": content})
        chat_path.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_chat(self, job_id: str) -> list:
        chat_path = self.config.base_dir / job_id / "chat.json"
        if chat_path.exists():
            try:
                return json.loads(chat_path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def load_existing_jobs(self) -> list[
        tuple[str, Path, Path, str, str, float, str, str, Optional[bool], Optional[bool], Optional[str], Optional[str]]
    ]:
        """
        Return list of (job_id, summary_path, transcript_path, title, youtube_url, created_at, status,
        transcript_source, prefer_youtube_captions, captions_attempted, captions_status, captions_detail)
        for jobs that have a summary or persisted chat history.
        """
        jobs = []
        for job_dir in self.config.base_dir.iterdir() if self.config.base_dir.exists() else []:
            if not job_dir.is_dir():
                continue
            summary_path = job_dir / "summary.txt"
            transcript_path = job_dir / "transcript.txt"
            chat_path = job_dir / "chat.json"
            title_path = job_dir / "title.txt"
            meta_path = job_dir / "meta.json"
            title = title_path.read_text(encoding="utf-8") if title_path.exists() else None
            youtube_url = None
            created_at = None
            status = "complete"
            transcript_source = None
            prefer_youtube_captions = None
            allow_whisper_fallback = None
            captions_attempted = None
            captions_status = None
            captions_detail = None
            meta_keys = set()
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    meta_keys = set(meta.keys())
                    youtube_url = meta.get("youtube_url")
                    created_at = meta.get("created_at")
                    status = meta.get("status", status)
                    transcript_source = meta.get("transcript_source")
                    prefer_youtube_captions = meta.get("prefer_youtube_captions")
                    allow_whisper_fallback = meta.get("allow_whisper_fallback")
                    captions_attempted = meta.get("captions_attempted")
                    captions_status = meta.get("captions_status")
                    captions_detail = meta.get("captions_detail")
                    if not title:
                        title = meta.get("title") or meta.get("display_name")
                except Exception:
                    pass
            if transcript_source is None and transcript_path.exists():
                # Best-effort inference for older jobs without meta.json.
                # We only positively identify Whisper when we see a whisper-extracted artifact;
                # otherwise leave as unknown.
                try:
                    has_whisper_artifact = any(
                        p.name.endswith(".whisper.wav") or ".whisper_" in p.name
                        for p in job_dir.iterdir()
                        if p.is_file()
                    )
                except Exception:
                    has_whisper_artifact = False
                transcript_source = "whisper" if has_whisper_artifact else "unknown"
            if created_at is None:
                try:
                    created_at = job_dir.stat().st_mtime
                except Exception:
                    created_at = 0.0
            if summary_path.exists() or chat_path.exists():
                # Opportunistically write meta.json if missing or missing key fields.
                needs_meta_write = (not meta_path.exists()) or (
                    not {
                        "created_at",
                        "youtube_url",
                        "status",
                        "transcript_source",
                        "prefer_youtube_captions",
                        "allow_whisper_fallback",
                        "captions_attempted",
                        "captions_status",
                        "captions_detail",
                    }.issubset(meta_keys)
                )
                if needs_meta_write:
                    try:
                        meta_payload = {
                            "job_id": job_dir.name,
                            "created_at": created_at,
                            "youtube_url": youtube_url,
                            "audio_path": None,
                            "title": title,
                            "display_name": title,
                            "status": status,
                            "prefer_youtube_captions": prefer_youtube_captions,
                            "allow_whisper_fallback": allow_whisper_fallback,
                            "transcript_source": transcript_source,
                            "captions_attempted": captions_attempted,
                            "captions_status": captions_status,
                            "captions_detail": captions_detail,
                        }
                        meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
                    except Exception:
                        pass
                jobs.append(
                    (
                        job_dir.name,
                        summary_path,
                        transcript_path,
                        title,
                        youtube_url,
                        created_at,
                        status,
                        transcript_source,
                        prefer_youtube_captions,
                        captions_attempted,
                        captions_status,
                        captions_detail,
                    )
                )
        return jobs
