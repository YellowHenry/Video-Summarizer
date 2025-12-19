import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


class AudioDownloader:
    def __init__(self, download_root: Optional[Path] = None):
        self.download_root = download_root or Path(tempfile.gettempdir())
        self.logger = logging.getLogger(__name__)

    def _get_title(self, url: str) -> Optional[str]:
        """Fetch the YouTube title without downloading media."""
        try:
            import yt_dlp
        except ImportError:
            return None

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get("title")
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Failed to fetch YouTube title: %s", exc)
            return None

    def _resolve_ffmpeg(self) -> Optional[str]:
        if os.getenv("FFMPEG_PATH"):
            candidate = Path(os.getenv("FFMPEG_PATH"))
            if candidate.exists():
                return str(candidate)
            resolved = shutil.which(os.getenv("FFMPEG_PATH"))
            if resolved:
                return resolved
        return shutil.which("ffmpeg")

    def download_youtube(self, url: str) -> Tuple[Path, Optional[str]]:
        """Attempt to download YouTube audio using ``yt-dlp``.

        Raises RuntimeError if ``yt-dlp`` is not available, as it's required
        for real YouTube downloads.
        """

        title = self._get_title(url)
        sanitized = url.replace("https://", "").replace("http://", "")
        # Remove all invalid Windows filename characters: < > : " / \ | ? *
        invalid_chars = '<>:"/\\|?*'
        filename = sanitized
        for char in invalid_chars:
            filename = filename.replace(char, "_")
        filename = filename[:80] or "youtube_audio"
        temp_dir = Path(tempfile.mkdtemp(prefix="yt_", dir=self.download_root))
        # Audio-only to shrink download size (we only need audio for transcription)
        output_path = temp_dir / f"{filename}.m4a"

        # Try to find yt-dlp executable first, then fall back to python -m yt_dlp
        yt_dlp = shutil.which("yt-dlp")
        if yt_dlp:
            command = [
                yt_dlp,
                "-o",
                str(output_path),
                "-f",
                "bestaudio[ext=m4a]/bestaudio",
                "-x",
                "--audio-format",
                "m4a",
                url,
            ]
        else:
            # Fall back to python -m yt_dlp (works with Windows Store Python)
            try:
                import yt_dlp  # noqa: F401
            except ImportError:
                raise RuntimeError(
                    "yt-dlp is required for YouTube downloads. "
                    "Install it with: pip install yt-dlp"
                )
            import sys
            command = [
                sys.executable,
                "-m",
                "yt_dlp",
                "-o",
                str(output_path),
                "-f",
                "bestaudio[ext=m4a]/bestaudio",
                "-x",
                "--audio-format",
                "m4a",
                url,
            ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            if output_path.exists():
                return output_path, title
            # If yt-dlp succeeded but file doesn't exist, check for alternative output
            # yt-dlp might have created a file with a different name/extension
            temp_files = list(temp_dir.glob("*"))
            if temp_files:
                # Return the first non-log file found
                for f in temp_files:
                    if f.suffix in (".mp4", ".webm", ".mkv", ".m4a", ".mp3") and f.name != "download_error.log":
                        return f, title
            raise RuntimeError(f"yt-dlp completed but no audio file was created. Output: {result.stdout}")
        except subprocess.CalledProcessError as exc:  # noqa: PERF203
            error_log = temp_dir / "download_error.log"
            error_msg = exc.stderr or exc.stdout or str(exc)
            error_log.write_text(error_msg, encoding="utf-8")
            raise RuntimeError(
                f"yt-dlp failed to download {url}. Error: {error_msg}"
            ) from exc

    def copy_local(self, source: Path) -> Path:
        if not source.exists():
            raise FileNotFoundError(f"Local file not found: {source}")
        temp_dir = Path(tempfile.mkdtemp(prefix="upload_", dir=self.download_root))

        if source.suffix.lower() in AUDIO_SUFFIXES:
            destination = temp_dir / source.name
            shutil.copy2(source, destination)
            return destination

        # Non-audio inputs: extract audio via ffmpeg and ignore video frames
        ffmpeg = self._resolve_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to extract audio from local video files")
        audio_path = temp_dir / f"{source.stem}.m4a"
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-acodec",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
            if audio_path.exists():
                return audio_path
            raise RuntimeError("ffmpeg did not produce an audio file from the local input")
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode() if isinstance(exc.stderr, (bytes, bytearray)) else exc.stderr
            raise RuntimeError(f"Failed to extract audio from {source}: {stderr or exc}") from exc
