import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import (
    HARDCODED_API_KEY,
    HARDCODED_COOKIES_FROM_BROWSER,
    HARDCODED_COOKIES_BROWSER_PROFILE,
)

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None


@dataclass
class SummarizerConfig:
    api_key: Optional[str] = HARDCODED_API_KEY or os.getenv("OPENAI_API_KEY") or os.getenv("SUMMARIZER_API_KEY")
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
    chunk_duration_seconds: int = int(os.getenv("SUMMARIZER_CHUNK_SECONDS", str(42 * 60)))  # default 42 minutes
    whisper_upload_limit_bytes: int = int(os.getenv("SUMMARIZER_WHISPER_LIMIT_BYTES", str(25 * 1024 * 1024)))


@dataclass
class SummarizeResult:
    summary: str
    transcript: Optional[str] = None
    transcript_source: Optional[str] = None


class CloudSummarizerClient:
    def __init__(self, config: Optional[SummarizerConfig] = None):
        self.config = config or SummarizerConfig()
        self.client = (
            OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                organization=self.config.organization,
                project=self.config.project,
                timeout=self.config.timeout,
            )
            if OpenAI and self.config.api_key
            else None
        )
        self.logger = logging.getLogger(__name__)

    def summarize(self, audio: Path, youtube_url: Optional[str] = None, prefer_youtube_captions: bool = True) -> SummarizeResult:
        """Summarize an audio file using configured cloud providers and return both the summary and raw transcript when available."""

        cloud_configured = bool(self.config.api_key or self.config.endpoint)
        transcript: Optional[str] = None
        transcript_source: Optional[str] = None

        # Try YouTube captions first if requested and OpenAI client is available
        if prefer_youtube_captions and youtube_url and self.client:
            captions = self._fetch_youtube_captions(youtube_url)
            if captions:
                transcript = captions
                transcript_source = "youtube_captions"
                try:
                    summary = self._summarize_with_openai(captions)
                    return SummarizeResult(summary=summary, transcript=transcript, transcript_source=transcript_source)
                except Exception as exc:  # noqa: BLE001
                    self.logger.error("Summarization from YouTube captions failed: %s", exc)
                    if cloud_configured:
                        raise

        # HTTP endpoint takes precedence when explicitly configured
        if self.config.endpoint:
            if not requests:
                raise RuntimeError(
                    "requests is required for HTTP summarization; install it via requirements.txt"
                )
            try:
                summary = self.summarize_via_http(audio)
                return SummarizeResult(summary=summary, transcript=transcript, transcript_source=transcript_source)
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
                    return SummarizeResult(summary=summary, transcript=transcript, transcript_source=transcript_source)
            except Exception as exc:  # noqa: BLE001
                self.logger.error("OpenAI summarization failed: %s", exc)
                if cloud_configured:
                    raise

        if cloud_configured:
            raise RuntimeError("Cloud summarization was configured but all providers failed")

        time.sleep(1)
        placeholder = (
            "Concise, conceptually faithful summary generated locally. Set "
            "OPENAI_API_KEY (or SUMMARIZER_API_KEY) to enable live Whisper + GPT "
            "summaries, or provide SUMMARIZER_HTTP_ENDPOINT for a custom "
            "compatible API."
        )
        return SummarizeResult(summary=placeholder, transcript=transcript, transcript_source=transcript_source)

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
        with audio.open("rb") as handle:
            files = {"file": (audio.name, handle, "application/octet-stream")}
            response = requests.post(
                self.config.endpoint,
                headers=headers,
                data={"model": self.config.model},
                files=files,
                timeout=self.config.timeout,
            )
        response.raise_for_status()
        payload = response.json()
        summary = payload.get("summary")
        if not summary:
            raise RuntimeError("Cloud summarization endpoint did not return a 'summary' field")
        return summary

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
        chrome_dir = Path(os.getenv("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
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
        
        for segment in segments:
            with segment.open("rb") as handle:
                transcription = self.client.audio.transcriptions.create(
                    model=self.config.transcription_model,
                    file=handle,
                    response_format="text",
                )
            normalized = str(transcription).strip()
            if normalized:
                transcripts.append(normalized)

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

    def _fetch_youtube_captions(self, url: str) -> Optional[str]:
        """Attempt to fetch English auto-generated YouTube captions."""
        try:
            import yt_dlp
        except ImportError:
            self.logger.info("yt-dlp not installed; cannot fetch YouTube captions")
            return None
        if not requests:
            self.logger.info("requests not installed; cannot fetch YouTube captions")
            return None

        extractor_args_list = [
            os.getenv("YTDLP_EXTRACTOR_ARGS", "youtube:player_client=tvhtml5desktop"),
            "youtube:player_client=web_embedded",
            "youtube:player_client=web_remix",
            "youtube:player_client=default",
        ]
        ydl_opts_base = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": False}
        browser_cookie = os.getenv("YTDLP_COOKIES_FROM_BROWSER") or HARDCODED_COOKIES_FROM_BROWSER
        profiles = self._candidate_profiles()

        try:
            info = None
            for extractor_arg in extractor_args_list:
                for profile in profiles:
                    ydl_opts = dict(ydl_opts_base)
                    ydl_opts["extractor_args"] = {"youtube": [extractor_arg]}
                    if browser_cookie:
                        ydl_opts["cookiesfrombrowser"] = (browser_cookie, profile, None, None)
                        ydl_opts.pop("cookiefile", None)
                    elif os.getenv("YTDLP_COOKIES"):
                        ydl_opts["cookiefile"] = os.getenv("YTDLP_COOKIES")
                        ydl_opts.pop("cookiesfrombrowser", None)
                    else:
                        raise RuntimeError("Cookies are required to fetch captions; set YTDLP_COOKIES_FROM_BROWSER or YTDLP_COOKIES.")
                    try:
                        self.logger.info("yt-dlp captions extractor=%s profile=%s", extractor_arg, profile)
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            info = ydl.extract_info(url, download=False)
                        if info:
                            raise_stop = True
                            break
                    except Exception as inner_exc:  # noqa: BLE001
                        self.logger.debug(
                            "Caption fetch failed extractor=%s profile=%s error=%s",
                            extractor_arg,
                            profile,
                            inner_exc,
                        )
                        continue
                else:
                    continue
                break
            if not info:
                return None
        except Exception as exc:  # noqa: BLE001
            self.logger.info("Failed to extract YouTube info for captions: %s", exc)
            return None

        captions = (info or {}).get("automatic_captions") or {}
        candidates = []
        for lang in ("en", "en-US", "en-GB"):
            if lang in captions:
                candidates.extend(captions[lang])
        for entry in candidates:
            caption_url = entry.get("url")
            if not caption_url:
                continue
            try:
                resp = requests.get(caption_url, timeout=self.config.timeout)
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
                            seg_resp = requests.get(line, timeout=self.config.timeout)
                            seg_resp.raise_for_status()
                            parts.append(seg_resp.text)
                        except Exception as seg_exc:  # noqa: BLE001
                            self.logger.info("Failed to fetch caption segment %s: %s", line, seg_exc)
                            continue
                    if parts:
                        text_content = "\n".join(parts)
                text = self._parse_caption_payload(text_content)
                if text:
                    self.logger.info("Using YouTube auto captions for %s", url)
                    return text
            except Exception as exc:  # noqa: BLE001
                self.logger.info("Failed to fetch/parse YouTube captions: %s", exc)
                continue
        return None

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
