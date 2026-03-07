import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .config import (
    get_openai_api_key,
    HARDCODED_COOKIES_FROM_BROWSER,
    HARDCODED_COOKIES_BROWSER_PROFILE,
)
from .proxy_egress import (
    build_requests_proxies,
    is_rate_limited_error,
    load_proxy_egress_config,
    proxy_index,
    redact_proxy,
    select_proxy_for_attempt,
    sleep_backoff,
)
from .youtube_url import (
    canonicalize_youtube_video_url,
    extract_youtube_video_id as parse_youtube_video_id,
    unwrap_ytdlp_video_info,
)
from .ytdlp_cookies import resolve_cookies_file

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None

try:
    from openai import BadRequestError, DefaultHttpxClient, OpenAI
except ImportError:  # pragma: no cover - optional dependency
    BadRequestError = None
    DefaultHttpxClient = None
    OpenAI = None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Match desktop-style authenticated YouTube access by default.
STRICT_COOKIES = _env_bool("YTDLP_STRICT_COOKIES", True)
# Optional non-yt-dlp fallback for operators who explicitly want it.
TRANSCRIPT_API_FALLBACK = _env_bool("YOUTUBE_TRANSCRIPT_API_FALLBACK", False)
PATH_FALLBACK = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _parse_csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


JS_RUNTIMES = _parse_csv_env("YTDLP_JS_RUNTIMES", "node")
REMOTE_COMPONENTS = _parse_csv_env("YTDLP_REMOTE_COMPONENTS", "")


def _js_runtime_dict() -> dict[str, dict]:
    return {runtime: {} for runtime in JS_RUNTIMES}


def _ensure_runtime_path() -> None:
    if not (os.getenv("PATH") or "").strip():
        os.environ["PATH"] = PATH_FALLBACK


@dataclass
class SummarizerConfig:
    api_key: Optional[str] = field(default_factory=get_openai_api_key)
    model: str = os.getenv("SUMMARIZER_MODEL", "gpt-4o-mini")
    transcription_model: str = os.getenv("SUMMARIZER_TRANSCRIBE_MODEL", "whisper-1")
    base_url: Optional[str] = os.getenv("OPENAI_BASE_URL") or os.getenv("AZURE_OPENAI_ENDPOINT")
    endpoint: Optional[str] = os.getenv("SUMMARIZER_HTTP_ENDPOINT")
    organization: Optional[str] = os.getenv("OPENAI_ORG")
    project: Optional[str] = os.getenv("OPENAI_PROJECT")
    timeout: int = int(os.getenv("SUMMARIZER_TIMEOUT", "120"))
    api_version: Optional[str] = os.getenv("AZURE_OPENAI_API_VERSION")
    ffmpeg_path: Optional[str] = os.getenv("FFMPEG_PATH")
    max_tokens: int = int(os.getenv("SUMMARIZER_MAX_TOKENS", "800"))
    job_chat_max_tokens: int = int(os.getenv("JOB_CHAT_MAX_TOKENS", "1500"))
    job_chat_auto_continue_enabled: bool = _env_bool("JOB_CHAT_AUTO_CONTINUE_ENABLED", True)
    job_chat_auto_continue_max_segments: int = int(os.getenv("JOB_CHAT_AUTO_CONTINUE_MAX_SEGMENTS", "4"))
    chunk_duration_seconds: int = int(os.getenv("SUMMARIZER_CHUNK_SECONDS", str(42 * 60)))  # default 42 minutes
    whisper_upload_limit_bytes: int = int(os.getenv("SUMMARIZER_WHISPER_LIMIT_BYTES", str(25 * 1024 * 1024)))
    openai_trust_env_proxy: bool = _env_bool("OPENAI_TRUST_ENV_PROXY", False)


@dataclass
class SummarizeResult:
    summary: str
    transcript: Optional[str] = None
    transcript_source: Optional[str] = None
    captions_attempted: bool = False
    captions_status: Optional[str] = None
    captions_detail: Optional[str] = None


class CloudSummarizerClient:
    def __init__(self, config: Optional[SummarizerConfig] = None):
        self.logger = logging.getLogger(__name__)
        self.config = config or SummarizerConfig()
        self.caption_proxy_config = load_proxy_egress_config(purpose="captions")
        self.http_proxy_config = load_proxy_egress_config(purpose="http_summarizer")
        self.client = None
        if OpenAI and self.config.api_key:
            client_kwargs = {
                "api_key": self.config.api_key,
                "base_url": self.config.base_url,
                "organization": self.config.organization,
                "project": self.config.project,
                "timeout": self.config.timeout,
            }
            if not self.config.openai_trust_env_proxy and DefaultHttpxClient:
                # Keep OpenAI transport direct unless explicitly overridden.
                client_kwargs["http_client"] = DefaultHttpxClient(timeout=self.config.timeout, trust_env=False)
            self.client = OpenAI(**client_kwargs)

        if self.client and self.config.openai_trust_env_proxy:
            self.logger.warning(
                "OPENAI_TRUST_ENV_PROXY=true; OpenAI requests may use environment proxy settings."
            )

    def _transcode_to_wav_retry(self, source: Path) -> Optional[Path]:
        """Create a retry-friendly WAV for Whisper when compressed input is rejected."""
        ffmpeg = self._resolve_ffmpeg()
        if not ffmpeg:
            return None
        target_dir = Path(tempfile.mkdtemp(prefix="whisper_retry_"))
        wav_path = target_dir / f"{source.stem}.retry.wav"
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
            if wav_path.exists():
                return wav_path
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Whisper WAV retry transcode failed for %s: %s", source.name, exc)
        return None

    def summarize_youtube_captions_only(self, youtube_url: str) -> SummarizeResult:
        """
        Caption-first summarization path for YouTube URLs that avoids audio download.
        This is useful in hosted workers where yt-dlp media download may require cookies.
        """
        if not youtube_url:
            raise RuntimeError("youtube_url is required for caption-first summarization")
        if not self.client:
            raise RuntimeError("OpenAI client not configured for caption-first summarization")

        captions, captions_source, captions_status, captions_detail = self._fetch_youtube_captions(youtube_url)
        if not captions:
            raise RuntimeError(
                "YouTube captions unavailable "
                f"(status={captions_status or 'unknown'}, detail={captions_detail or 'none'})"
            )

        summary = self._summarize_with_openai(captions)
        return SummarizeResult(
            summary=summary,
            transcript=captions,
            transcript_source=captions_source or "youtube_captions",
            captions_attempted=True,
            captions_status=captions_status,
            captions_detail=captions_detail,
        )

    def _resolve_caption_cookie_source(self) -> Optional[str]:
        """
        Resolve browser-cookie source for yt-dlp caption extraction.
        In Cloud Run, default to no browser cookies unless explicitly configured.
        """
        from_env = os.getenv("YTDLP_COOKIES_FROM_BROWSER")
        if from_env is not None:
            cleaned = from_env.strip()
            return cleaned or None
        if os.getenv("K_SERVICE"):
            return None
        return HARDCODED_COOKIES_FROM_BROWSER

    def _extract_youtube_video_id(self, url: str) -> Optional[str]:
        return parse_youtube_video_id(url)

    def _fetch_youtube_captions_via_transcript_api(
        self, url: str
    ) -> tuple[Optional[str], Optional[str], str, Optional[str]]:
        """
        Optional caption fallback using youtube-transcript-api.
        """
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except Exception:
            return (None, None, "transcript_api_missing", "youtube-transcript-api not installed")

        video_id = self._extract_youtube_video_id(url)
        if not video_id:
            return (None, None, "invalid_youtube_url", "Could not parse YouTube video id from URL")

        try:
            transcript = YouTubeTranscriptApi().fetch(video_id, languages=["en", "en-US", "en-GB"])
        except Exception as exc:  # noqa: BLE001
            return (None, None, "transcript_api_failed", str(exc))

        lines: list[str] = []
        for item in transcript:
            text = None
            if hasattr(item, "text"):
                text = getattr(item, "text", None)
            elif isinstance(item, dict):
                text = item.get("text")
            if not text:
                continue
            cleaned = str(text).replace("\u00a0", " ").replace("\n", " ").strip()
            if cleaned:
                lines.append(cleaned)
        parsed = "\n".join(lines).strip()
        if not parsed:
            return (None, None, "transcript_api_empty", "Transcript API returned empty text")
        return (parsed, "youtube_captions", "success", None)

    def summarize(self, audio: Path, youtube_url: Optional[str] = None, prefer_youtube_captions: bool = True) -> SummarizeResult:
        """Summarize an audio file using configured cloud providers and return both the summary and raw transcript when available."""

        cloud_configured = bool(self.config.api_key or self.config.endpoint)
        transcript: Optional[str] = None
        transcript_source: Optional[str] = None
        captions_attempted = False
        captions_status: Optional[str] = None
        captions_detail: Optional[str] = None

        # Try YouTube captions first if requested and OpenAI client is available
        if prefer_youtube_captions and youtube_url and self.client:
            captions_attempted = True
            captions, captions_source, captions_status, captions_detail = self._fetch_youtube_captions(youtube_url)
            if captions:
                transcript = captions
                transcript_source = captions_source or "youtube_captions"
                try:
                    summary = self._summarize_with_openai(captions)
                    return SummarizeResult(
                        summary=summary,
                        transcript=transcript,
                        transcript_source=transcript_source,
                        captions_attempted=captions_attempted,
                        captions_status=captions_status,
                        captions_detail=captions_detail,
                    )
                except Exception as exc:  # noqa: BLE001
                    self.logger.error("Summarization from YouTube captions failed: %s", exc)
                    if cloud_configured:
                        raise
            else:
                self.logger.info(
                    "YouTube captions unavailable; falling back to Whisper. status=%s detail=%s",
                    captions_status or "unknown",
                    captions_detail or "",
                )
        elif prefer_youtube_captions and youtube_url and not self.client:
            captions_status = "skipped_openai_not_configured"
            captions_detail = "OpenAI client not configured; caption-first mode requires OpenAI to summarize captions."

        # HTTP endpoint takes precedence when explicitly configured
        if self.config.endpoint:
            if not requests:
                raise RuntimeError(
                    "requests is required for HTTP summarization; install it via requirements.txt"
                )
            try:
                summary = self.summarize_via_http(audio)
                return SummarizeResult(
                    summary=summary,
                    transcript=transcript,
                    transcript_source=transcript_source,
                    captions_attempted=captions_attempted,
                    captions_status=captions_status,
                    captions_detail=captions_detail,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.error("HTTP summarization failed: %s", exc)
                if not self.client:
                    raise

        # Direct OpenAI/Azure path
        if self.config.api_key:
            if not self.client:
                raise RuntimeError("openai package is required when OPENAI_API_KEY is set")
            try:
                transcript = self._transcribe_with_openai(audio)
                transcript_source = "whisper"
                if transcript:
                    summary = self._summarize_with_openai(transcript)
                    return SummarizeResult(
                        summary=summary,
                        transcript=transcript,
                        transcript_source=transcript_source,
                        captions_attempted=captions_attempted,
                        captions_status=captions_status,
                        captions_detail=captions_detail,
                    )
            except Exception as exc:  # noqa: BLE001
                self.logger.error("OpenAI summarization failed: %s", exc)
                if cloud_configured:
                    raise

        if cloud_configured:
            raise RuntimeError("Cloud summarization was configured but all providers failed")

        time.sleep(1)
        placeholder = (
            "Concise, conceptually faithful summary generated locally. Set "
            "backend/config.py OPENAI_API_KEY (or env OPENAI_API_KEY/SUMMARIZER_API_KEY) "
            "to enable live Whisper + GPT "
            "summaries, or provide SUMMARIZER_HTTP_ENDPOINT for a custom "
            "compatible API."
        )
        return SummarizeResult(
            summary=placeholder,
            transcript=transcript,
            transcript_source=transcript_source,
            captions_attempted=captions_attempted,
            captions_status=captions_status,
            captions_detail=captions_detail,
        )

    def _resolve_ffmpeg(self) -> Optional[str]:
        import shutil

        if self.config.ffmpeg_path:
            configured = Path(self.config.ffmpeg_path)
            if configured.exists():
                return str(configured)
            fallback = shutil.which(self.config.ffmpeg_path)
            if fallback:
                return fallback
            self.logger.warning("Configured FFMPEG_PATH does not exist: %s", self.config.ffmpeg_path)
        return shutil.which("ffmpeg")

    def _resolve_ffprobe(self) -> Optional[str]:
        import shutil

        if self.config.ffmpeg_path:
            configured = Path(self.config.ffmpeg_path)
            candidate = configured.with_name("ffprobe")
            if candidate.exists():
                return str(candidate)
        return shutil.which("ffprobe")

    def summarize_via_http(self, audio: Path) -> str:
        if not requests:
            raise RuntimeError("requests is required for HTTP summarization; install it via requirements.txt")
        if not self.config.endpoint:
            raise RuntimeError("SUMMARIZER_HTTP_ENDPOINT must be set for HTTP summarization")
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        proxy_config = self.http_proxy_config
        attempts = proxy_config.max_retries if proxy_config.enabled else 1
        attempts = max(1, attempts)
        last_error: Optional[Exception] = None
        for attempt_idx in range(attempts):
            proxy_url = select_proxy_for_attempt(proxy_config, attempt_idx, stable_key=self.config.endpoint)
            req_proxies = build_requests_proxies(proxy_url)
            proxy_label = redact_proxy(proxy_url)
            proxy_slot = proxy_index(proxy_config, proxy_url)
            try:
                with audio.open("rb") as handle:
                    files = {"file": (audio.name, handle, "application/octet-stream")}
                    response = requests.post(
                        self.config.endpoint,
                        headers=headers,
                        data={"model": self.config.model},
                        files=files,
                        timeout=self.config.timeout,
                        proxies=req_proxies,
                    )
                response.raise_for_status()
                payload = response.json()
                summary = payload.get("summary")
                if not summary:
                    raise RuntimeError("Cloud summarization endpoint did not return a 'summary' field")
                return summary
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if is_rate_limited_error(str(exc)) and attempt_idx + 1 < attempts:
                    self.logger.warning(
                        "HTTP summarizer rate-limited (attempt %s/%s, egress=%s, proxy_slot=%s); retrying.",
                        attempt_idx + 1,
                        attempts,
                        proxy_label,
                        proxy_slot or "n/a",
                    )
                    sleep_backoff(proxy_config, attempt_idx)
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("Cloud summarization endpoint failed with unknown error")

    def _extract_audio(self, source: Path) -> Path:
        """Extract or recompress audio to reduce file size for transcription."""
        import subprocess
        import tempfile

        ffmpeg = self._resolve_ffmpeg()
        if not ffmpeg:
            # If ffmpeg not available, return original (may be too large)
            self.logger.warning("ffmpeg not available, using original audio file")
            return source

        destination_dir = source.parent if source.parent.exists() else Path(tempfile.mkdtemp(prefix="audio_extract_"))
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            destination_dir = Path(tempfile.mkdtemp(prefix="audio_extract_"))
            destination_dir.mkdir(parents=True, exist_ok=True)
        bitrate_steps = [64, 48, 32, 24, 16, 12, 8]

        for bitrate in bitrate_steps:
            audio_path = destination_dir / f"{source.stem}.whisper_{bitrate}k.m4a"
            command = [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-vn",
                "-acodec",
                "aac",
                "-b:a",
                f"{bitrate}k",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(audio_path),
            ]
            try:
                subprocess.run(command, check=True, capture_output=True)
                if audio_path.exists():
                    size_bytes = audio_path.stat().st_size
                    size_mb = size_bytes / (1024 * 1024)
                    if size_bytes <= self.config.whisper_upload_limit_bytes:
                        self.logger.info(
                            "Extracted audio at %sk for Whisper: %s (%.1f MB)",
                            bitrate,
                            audio_path,
                            size_mb,
                        )
                        return audio_path
                    self.logger.warning(
                        "Audio at %sk still too large for Whisper (%.1f MB > limit %.1f MB); trying lower bitrate",
                        bitrate,
                        size_mb,
                        self.config.whisper_upload_limit_bytes / (1024 * 1024),
                    )
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode() if isinstance(exc.stderr, (bytes, bytearray)) else exc.stderr
                self.logger.warning("Audio extraction at %sk failed: %s", bitrate, stderr or exc)

        # Final attempt: uncompressed WAV at 16k mono (may still exceed limit)
        wav_path = destination_dir / f"{source.stem}.whisper.wav"
        wav_command = [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ]
        try:
            subprocess.run(wav_command, check=True, capture_output=True)
            if wav_path.exists() and wav_path.stat().st_size <= self.config.whisper_upload_limit_bytes:
                size_mb = wav_path.stat().st_size / (1024 * 1024)
                self.logger.info("Extracted audio (pcm) for Whisper: %s (%.1f MB)", wav_path, size_mb)
                return wav_path
            if wav_path.exists():
                size_mb = wav_path.stat().st_size / (1024 * 1024)
                raise RuntimeError(
                    f"Whisper upload would exceed limit even after compression: {size_mb:.1f} MB "
                    f">(limit {self.config.whisper_upload_limit_bytes / (1024 * 1024):.1f} MB)"
                )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode() if isinstance(exc.stderr, (bytes, bytearray)) else exc.stderr
            self.logger.warning("Audio extraction via pcm fallback failed: %s", stderr or exc)

        # If everything failed, fall back to original (may still exceed limit and fail upstream)
        return source

    def _candidate_profiles(self) -> list[str]:
        """Return a single best Chrome profile to try."""
        preferred = os.getenv("YTDLP_COOKIES_FROM_BROWSER_PROFILE") or HARDCODED_COOKIES_BROWSER_PROFILE or "Default"

        local_app_data = (os.getenv("LOCALAPPDATA") or "").strip()
        if local_app_data:
            chrome_dir = Path(local_app_data) / "Google" / "Chrome" / "User Data"
        else:
            home = Path.home()
            candidates = [
                home / ".config" / "google-chrome",
                home / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome",
                home / ".config" / "chromium",
            ]
            chrome_dir = candidates[0]
            for candidate in candidates:
                if candidate.exists():
                    chrome_dir = candidate
                    break

        existing: list[str] = []
        if chrome_dir.exists():
            for path in chrome_dir.iterdir():
                if path.is_dir() and (path.name == "Default" or path.name.startswith("Profile ")):
                    existing.append(path.name)

        if preferred in existing:
            return [preferred]
        if existing:
            return [existing[0]]
        return ["Default"]

    def _probe_duration_seconds(self, audio: Path) -> Optional[float]:
        ffprobe = self._resolve_ffprobe()
        if not ffprobe:
            return None
        command = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio),
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            return float(result.stdout.strip())
        except Exception:  # noqa: BLE001
            return None

    def _segment_audio(self, audio: Path) -> list[Path]:
        """Chunk audio into <= chunk_duration_seconds segments to stay within Whisper limits."""
        duration = self._probe_duration_seconds(audio)
        if self.config.chunk_duration_seconds <= 0:
            return [audio]
        if duration and duration <= self.config.chunk_duration_seconds:
            return [audio]

        ffmpeg = self._resolve_ffmpeg()
        if not ffmpeg:
            self.logger.warning("ffmpeg not available; cannot segment audio. Proceeding with single upload.")
            return [audio]

        temp_dir = Path(tempfile.mkdtemp(prefix="audio_segments_"))
        pattern = temp_dir / f"{audio.stem}_part_%03d{audio.suffix}"
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(audio),
            "-f",
            "segment",
            "-segment_time",
            str(self.config.chunk_duration_seconds),
            "-reset_timestamps",
            "1",
            "-map",
            "0",
            "-c",
            "copy",
            str(pattern),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
            segments = sorted(temp_dir.glob(f"{audio.stem}_part_*{audio.suffix}"))
            if not segments:
                self.logger.warning("Audio segmentation produced no parts; using original audio")
                return [audio]
            self.logger.info(
                "Segmented audio into %d part(s) of ~%d seconds each for Whisper",
                len(segments),
                self.config.chunk_duration_seconds,
            )
            return segments
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode() if isinstance(exc.stderr, (bytes, bytearray)) else exc.stderr
            self.logger.warning("Audio segmentation failed; using original audio: %s", stderr or exc)
            return [audio]

    def _transcribe_with_openai(self, audio: Path) -> str:
        if not self.client:
            raise RuntimeError("openai package is not installed")
        
        # Extract audio first to reduce file size (Whisper only needs audio)
        audio_file = self._extract_audio(audio)
        segments = self._segment_audio(audio_file)
        transcripts: list[str] = []
        
        for idx, segment in enumerate(segments, start=1):
            seg_size_mb = segment.stat().st_size / (1024 * 1024) if segment.exists() else 0.0
            seg_duration = self._probe_duration_seconds(segment)
            self.logger.info(
                "Whisper request segment=%s/%s file=%s size_mb=%.2f duration_s=%s model=%s",
                idx,
                len(segments),
                segment.name,
                seg_size_mb,
                f"{seg_duration:.1f}" if seg_duration else "unknown",
                self.config.transcription_model,
            )

            retry_candidates: list[Path] = [segment]
            wav_retry = self._transcode_to_wav_retry(segment)
            if wav_retry:
                retry_candidates.append(wav_retry)

            last_error: Optional[Exception] = None
            for candidate_idx, candidate in enumerate(retry_candidates, start=1):
                cand_size_mb = candidate.stat().st_size / (1024 * 1024) if candidate.exists() else 0.0
                try:
                    with candidate.open("rb") as handle:
                        transcription = self.client.audio.transcriptions.create(
                            model=self.config.transcription_model,
                            file=handle,
                            response_format="text",
                        )
                    normalized = str(transcription).strip()
                    if normalized:
                        transcripts.append(normalized)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    error_body = getattr(exc, "body", None)
                    if not error_body:
                        response = getattr(exc, "response", None)
                        if response is not None:
                            try:
                                error_body = response.text
                            except Exception:  # noqa: BLE001
                                error_body = None
                    self.logger.error(
                        "Whisper transcription failed segment=%s/%s attempt=%s/%s file=%s size_mb=%.2f error=%s body=%s",
                        idx,
                        len(segments),
                        candidate_idx,
                        len(retry_candidates),
                        candidate.name,
                        cand_size_mb,
                        exc,
                        str(error_body)[:500] if error_body else "n/a",
                    )
                    if (
                        BadRequestError
                        and isinstance(exc, BadRequestError)
                        and candidate_idx < len(retry_candidates)
                    ):
                        self.logger.warning(
                            "Retrying Whisper segment=%s with WAV fallback after bad request.", idx
                        )
                        continue
                    raise
            else:
                if last_error:
                    raise last_error

        if not transcripts:
            return (
                "Audio contained no discernible speech. Produce a concise summary "
                "noting the absence of spoken content."
            )

        return "\n\n".join(transcripts)

    def _summarize_with_openai(self, transcript: str) -> str:
        if not self.client:
            raise RuntimeError("openai package is not installed")
        prompt = (
            "Summarize the following transcript into a concise, accurate recap "
            "that highlights key topics, math steps, and conclusions. Make it detailed. Avoid "
            "hallucinating details. Transcript:\n" + transcript
        )
        completion = self.client.chat.completions.create(
            model=self.config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=self.config.max_tokens,
        )
        choice = completion.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            self.logger.warning(
                "Summary hit max_tokens limit (%s); increase SUMMARIZER_MAX_TOKENS if you want longer output",
                self.config.max_tokens,
            )
        return choice.message.content.strip()

    def _fetch_youtube_captions(self, url: str) -> tuple[Optional[str], Optional[str], str, Optional[str]]:
        """
        Attempt to fetch English YouTube captions and return:
        (text, transcript_source, status, detail)
        where transcript_source is one of "youtube_subtitles" or "youtube_auto_captions" on success.
        """
        transcript_api_result: Optional[tuple[Optional[str], Optional[str], str, Optional[str]]] = None
        target_video_id = self._extract_youtube_video_id(url)
        normalized_url = canonicalize_youtube_video_url(url)

        def _maybe_try_transcript_api() -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
            nonlocal transcript_api_result
            if not TRANSCRIPT_API_FALLBACK:
                return (None, None, None, None)
            if transcript_api_result is None:
                transcript_api_result = self._fetch_youtube_captions_via_transcript_api(url)
            text, source, status, detail = transcript_api_result
            if text:
                self.logger.info("Using transcript-api fallback captions for %s", url)
            return (text, source, status, detail)

        _ensure_runtime_path()

        try:
            import yt_dlp
        except ImportError:
            text, source, status, detail = _maybe_try_transcript_api()
            if text:
                return (text, source, status or "success", detail)
            if status and detail:
                return (None, None, "yt_dlp_missing", f"yt-dlp not installed; transcript_api={status}: {detail}")
            return (None, None, "yt_dlp_missing", "yt-dlp not installed")
        if not requests:
            return (None, None, "requests_missing", "requests not installed")

        extractor_args_list = [
            os.getenv("YTDLP_EXTRACTOR_ARGS", "youtube:player_client=tvhtml5desktop"),
            "youtube:player_client=web_embedded",
            "youtube:player_client=web_remix",
            "youtube:player_client=default",
        ]
        ydl_opts_base = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "noplaylist": True,
            # Caption metadata can still be present when playable formats are blocked/challenged.
            # We only need subtitle/automatic_captions here, so don't hard-fail on missing formats.
            "ignore_no_formats_error": True,
        }
        browser_cookie = self._resolve_caption_cookie_source()
        cookie_file = resolve_cookies_file()
        cookie_attempts: list[tuple[str, Optional[str]]] = []
        if browser_cookie:
            for profile in self._candidate_profiles():
                cookie_attempts.append(("browser", profile))
        if cookie_file:
            cookie_attempts.append(("file", cookie_file))
        if not cookie_attempts and STRICT_COOKIES:
            text, source, status, detail = _maybe_try_transcript_api()
            if text:
                return (text, source, status or "success", detail)
            if status and detail:
                return (
                    None,
                    None,
                    "cookie_auth_required",
                    "YTDLP_STRICT_COOKIES=true and no cookies were configured; "
                    f"transcript_api={status}: {detail}",
                )
            return (
                None,
                None,
                "cookie_auth_required",
                "YTDLP_STRICT_COOKIES=true and no cookies were configured. "
                "Set YTDLP_COOKIES_FILE/YTDLP_COOKIES_B64/YTDLP_COOKIES, or disable strict mode explicitly.",
            )
        if not STRICT_COOKIES:
            cookie_attempts.append(("none", None))

        proxy_config = self.caption_proxy_config
        attempts = proxy_config.max_retries if proxy_config.enabled else 1
        attempts = max(1, attempts)
        last_status = "extract_info_failed"
        last_detail: Optional[str] = None

        for attempt_idx in range(attempts):
            proxy_url = select_proxy_for_attempt(proxy_config, attempt_idx, stable_key=normalized_url)
            proxy_slot = proxy_index(proxy_config, proxy_url)
            proxy_label = redact_proxy(proxy_url)
            req_proxies = build_requests_proxies(proxy_url)

            info = None
            last_inner_error: Optional[str] = None
            for extractor_arg in extractor_args_list:
                for cookie_mode, cookie_value in cookie_attempts:
                    ydl_opts = dict(ydl_opts_base)
                    ydl_opts["extractor_args"] = {"youtube": [extractor_arg]}
                    if JS_RUNTIMES:
                        ydl_opts["js_runtimes"] = _js_runtime_dict()
                    if REMOTE_COMPONENTS:
                        ydl_opts["remote_components"] = REMOTE_COMPONENTS
                    if proxy_url:
                        ydl_opts["proxy"] = proxy_url
                    else:
                        ydl_opts.pop("proxy", None)
                    if cookie_mode == "browser" and browser_cookie and cookie_value:
                        ydl_opts["cookiesfrombrowser"] = (browser_cookie, cookie_value, None, None)
                        ydl_opts.pop("cookiefile", None)
                    elif cookie_mode == "file" and cookie_value:
                        ydl_opts["cookiefile"] = cookie_value
                        ydl_opts.pop("cookiesfrombrowser", None)
                    else:
                        ydl_opts.pop("cookiesfrombrowser", None)
                        ydl_opts.pop("cookiefile", None)
                    try:
                        if cookie_mode == "browser":
                            cookie_label = f"browser:{browser_cookie} profile:{cookie_value}"
                        elif cookie_mode == "file":
                            cookie_label = f"file:{cookie_value}"
                        else:
                            cookie_label = "none"
                        self.logger.info(
                            "yt-dlp captions extractor=%s cookies=%s egress=%s proxy_slot=%s attempt=%s/%s",
                            extractor_arg,
                            cookie_label,
                            proxy_label,
                            proxy_slot or "n/a",
                            attempt_idx + 1,
                            attempts,
                        )
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            info = ydl.extract_info(normalized_url, download=False)
                        if info:
                            break
                    except Exception as inner_exc:  # noqa: BLE001
                        last_inner_error = str(inner_exc)
                        self.logger.debug(
                            "Caption fetch failed extractor=%s cookie_mode=%s proxy_slot=%s error=%s",
                            extractor_arg,
                            cookie_mode,
                            proxy_slot or "n/a",
                            inner_exc,
                        )
                        continue
                else:
                    continue
                break

            if not info:
                last_status = "extract_info_failed"
                last_detail = last_inner_error
                if is_rate_limited_error(last_inner_error) and attempt_idx + 1 < attempts:
                    self.logger.warning(
                        "Rate-limited while fetching caption metadata (egress=%s proxy_slot=%s attempt=%s/%s). Rotating.",
                        proxy_label,
                        proxy_slot or "n/a",
                        attempt_idx + 1,
                        attempts,
                    )
                    sleep_backoff(proxy_config, attempt_idx)
                    continue
                break

            wrapper_detected = isinstance(info, dict) and info.get("entries") is not None
            resolved_info = unwrap_ytdlp_video_info(info, target_video_id)
            wrapper_unwrapped = wrapper_detected and resolved_info is not info
            info_for_captions = resolved_info if isinstance(resolved_info, dict) else (info if isinstance(info, dict) else {})
            subtitles = (info_for_captions or {}).get("subtitles") or {}
            auto_captions = (info_for_captions or {}).get("automatic_captions") or {}
            self.logger.info(
                "yt-dlp captions metadata submitted_url=%s normalized_url=%s noplaylist=true target_video_id=%s yt_info_type=%s yt_info_id=%s wrapper_unwrapped=%s subtitles_lang_count=%s auto_captions_lang_count=%s",
                url,
                normalized_url,
                target_video_id or "unknown",
                "playlist_wrapper" if wrapper_detected else "video",
                str(info_for_captions.get("id") or "unknown"),
                str(bool(wrapper_unwrapped)).lower(),
                len(subtitles),
                len(auto_captions),
            )
            if wrapper_detected and not wrapper_unwrapped and target_video_id:
                self.logger.warning(
                    "yt-dlp captions metadata returned playlist wrapper without matching entry submitted_url=%s normalized_url=%s target_video_id=%s",
                    url,
                    normalized_url,
                    target_video_id,
                )
            retryable_fetch = False
            for source_name, captions_map in (("youtube_subtitles", subtitles), ("youtube_auto_captions", auto_captions)):
                candidates = []
                for lang in ("en", "en-US", "en-GB"):
                    if lang in captions_map:
                        candidates.extend(captions_map[lang])
                if not candidates:
                    continue

                last_fetch_error: Optional[str] = None
                for entry in candidates:
                    caption_url = entry.get("url")
                    if not caption_url:
                        continue
                    try:
                        resp = requests.get(caption_url, timeout=self.config.timeout, proxies=req_proxies)
                        resp.raise_for_status()
                        text_content = resp.text
                        # Some caption endpoints return an HLS playlist (.m3u8) pointing to VTT chunks; follow it.
                        if text_content.lstrip().startswith("#EXTM3U"):
                            parts: list[str] = []
                            for line in text_content.splitlines():
                                line = line.strip()
                                if not line or line.startswith("#"):
                                    continue
                                try:
                                    seg_resp = requests.get(line, timeout=self.config.timeout, proxies=req_proxies)
                                    seg_resp.raise_for_status()
                                    parts.append(seg_resp.text)
                                except Exception as seg_exc:  # noqa: BLE001
                                    last_fetch_error = str(seg_exc)
                                    self.logger.info(
                                        "Failed to fetch caption segment proxy_slot=%s url=%s error=%s",
                                        proxy_slot or "n/a",
                                        line,
                                        seg_exc,
                                    )
                                    continue
                            if parts:
                                text_content = "\n".join(parts)
                        text = self._parse_caption_payload(text_content)
                        if text:
                            self.logger.info(
                                "Using YouTube captions source=%s for %s (proxy_slot=%s)",
                                source_name,
                                url,
                                proxy_slot or "n/a",
                            )
                            return (text, source_name, "success", None)
                        last_fetch_error = "parsed captions were empty"
                    except Exception as exc:  # noqa: BLE001
                        last_fetch_error = str(exc)
                        self.logger.info("Failed to fetch/parse YouTube captions: %s", exc)
                        continue

                last_status = "fetch_parse_failed"
                last_detail = last_fetch_error
                if is_rate_limited_error(last_fetch_error) and attempt_idx + 1 < attempts:
                    retryable_fetch = True
                    break
                return (None, None, "fetch_parse_failed", last_fetch_error)

            if retryable_fetch:
                self.logger.warning(
                    "Rate-limited while fetching caption payload (egress=%s proxy_slot=%s attempt=%s/%s). Rotating.",
                    proxy_label,
                    proxy_slot or "n/a",
                    attempt_idx + 1,
                    attempts,
                )
                sleep_backoff(proxy_config, attempt_idx)
                continue

            last_status = "no_en_captions"
            if wrapper_detected:
                last_detail = (
                    "No English captions found in subtitles/automatic_captions "
                    f"(wrapper_result_detected=true, wrapper_unwrapped={str(bool(wrapper_unwrapped)).lower()})"
                )
            else:
                last_detail = "No English captions found in subtitles/automatic_captions"
            break

        text, source, status, transcript_detail = _maybe_try_transcript_api()
        if text:
            return (text, source, status or "success", transcript_detail)

        detail = last_detail
        if status and transcript_detail:
            detail = f"yt_dlp={last_detail or 'unknown'}; transcript_api={status}: {transcript_detail}"
        return (None, None, last_status, detail)

    def _parse_caption_payload(self, caption_text: str) -> str:
        """Parse YouTube captions returned as either VTT or JSON3 into human-readable text."""
        # Try JSON3 first (contains "events" with "segs"/"utf8")
        try:
            payload = json.loads(caption_text)
        except (ValueError, TypeError):
            payload = None

        if isinstance(payload, dict) and "events" in payload:
            lines: list[str] = []
            for event in payload.get("events", []):
                segs = event.get("segs") or []
                pieces: list[str] = []
                for seg in segs:
                    text = seg.get("utf8")
                    if not text:
                        continue
                    cleaned = text.replace("\u00a0", " ").replace("\n", " ").strip()
                    if cleaned:
                        pieces.append(cleaned)
                if pieces:
                    line = " ".join(pieces)
                    # Collapse multiple spaces for readability
                    line = " ".join(line.split())
                    if line:
                        lines.append(line)
            parsed = "\n".join(lines).strip()
            if parsed:
                return parsed

        # Fallback: simple VTT parsing
        lines = []
        for line in caption_text.splitlines():
            if not line or line.startswith("WEBVTT") or "-->" in line:
                continue
            lines.append(line.strip())
        text = "\n".join(lines).strip()
        return text or ""
