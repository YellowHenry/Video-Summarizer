import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from .config import HARDCODED_COOKIES_FROM_BROWSER, HARDCODED_COOKIES_BROWSER_PROFILE

AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
DEFAULT_EXTRACTOR_ARGS = os.getenv("YTDLP_EXTRACTOR_ARGS", "youtube:player_client=tvhtml5desktop")
# Prefer clients that avoid PO-token and SABR issues; try a short, safe list.
FALLBACK_EXTRACTOR_ARGS = [
    DEFAULT_EXTRACTOR_ARGS,
    "youtube:player_client=web_embedded",
    "youtube:player_client=web_remix",
    "youtube:player_client=default",
]
COOKIES_FILE = os.getenv("YTDLP_COOKIES")  # optional path to cookies.txt (Netscape format)
COOKIES_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER") or HARDCODED_COOKIES_FROM_BROWSER  # optional browser name for --cookies-from-browser
COOKIES_BROWSER_PROFILE = os.getenv("YTDLP_COOKIES_FROM_BROWSER_PROFILE") or HARDCODED_COOKIES_BROWSER_PROFILE
CHROME_USER_DATA = Path(os.getenv("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


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

        for extractor_arg in FALLBACK_EXTRACTOR_ARGS:
            for profile in self._candidate_profiles():
                browser_spec = (COOKIES_BROWSER, profile, None, None) if COOKIES_BROWSER else None
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "extract_flat": False,
                    "extractor_args": {"youtube": [extractor_arg]},
                }
                if browser_spec:
                    ydl_opts["cookiesfrombrowser"] = browser_spec
                elif COOKIES_FILE:
                    ydl_opts["cookiefile"] = COOKIES_FILE
                try:
                    self.logger.info("yt-dlp title attempt extractor=%s cookies=browser:%s profile:%s", extractor_arg, COOKIES_BROWSER, profile)
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        if info and info.get("title"):
                            return info["title"]
                except Exception as exc:  # noqa: BLE001
                    self.logger.debug("Title fetch failed extractor=%s profile=%s cookies=%s error=%s", extractor_arg, profile, browser_spec or COOKIES_FILE or "none", exc)
                    continue
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
        extractor_arg = DEFAULT_EXTRACTOR_ARGS

        if yt_dlp:
            base_common = [
                yt_dlp,
                "-o",
                str(output_path),
                "-f",
                "bestaudio[ext=m4a]/bestaudio",
                "-x",
                "--audio-format",
                "m4a",
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
            base_common = [
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
            ]
        attempts = []
        profiles = self._candidate_profiles()
        for extractor_arg in FALLBACK_EXTRACTOR_ARGS:
            for profile in profiles:
                cookies_flags: list[str] = []
                if COOKIES_BROWSER:
                    cookies_flags = ["--cookies-from-browser", f"{COOKIES_BROWSER}:{profile}"]
                elif COOKIES_FILE:
                    cookies_flags = ["--cookies", COOKIES_FILE]
                else:
                    raise RuntimeError("Cookies required: set YTDLP_COOKIES_FROM_BROWSER (e.g., 'edge') or YTDLP_COOKIES to a cookies.txt file.")

                attempt_with = base_common + ["--extractor-args", extractor_arg] + cookies_flags + [url]
                attempts.append((extractor_arg, profile, cookies_flags, attempt_with))
        last_error: Optional[str] = None
        for extractor_arg, profile, cookies_flags, attempt in attempts:
            self.logger.info("yt-dlp attempt extractor=%s profile=%s cookies=%s", extractor_arg, profile, " ".join(cookies_flags))
            try:
                result = subprocess.run(attempt, check=True, capture_output=True, text=True)
                if output_path.exists():
                    return output_path, title
                temp_files = list(temp_dir.glob("*"))
                if temp_files:
                    for f in temp_files:
                        if f.suffix in (".mp4", ".webm", ".mkv", ".m4a", ".mp3") and f.name != "download_error.log":
                            return f, title
                last_error = f"yt-dlp completed but no audio file was created. Output: {result.stdout}"
            except subprocess.CalledProcessError as exc:  # noqa: PERF203
                err_text = exc.stderr or exc.stdout or str(exc)
                last_error = err_text
                continue

        error_log = temp_dir / "download_error.log"
        if last_error:
            error_log.write_text(last_error, encoding="utf-8")
        hint = ""
        if "403" in (last_error or "") or "SABR" in (last_error or ""):
            hint = " Hint: this video may require cookies/login; set YTDLP_COOKIES to a cookies.txt path or try another client."
        if "DPAPI" in (last_error or "") or "_parse_browser_specification" in (last_error or "") or "Could not copy" in (last_error or ""):
            hint += " Hint: browser cookies could not be read; close the browser or use YTDLP_COOKIES to supply an exported cookies.txt."
        raise RuntimeError(f"yt-dlp failed to download {url}. Error: {last_error}{hint}")

    def _candidate_profiles(self) -> list[str]:
        """Return a single best Chrome profile to try."""
        preferred = COOKIES_BROWSER_PROFILE or "Default"
        existing: list[str] = []
        if CHROME_USER_DATA.exists():
            for path in CHROME_USER_DATA.iterdir():
                if path.is_dir() and (path.name == "Default" or path.name.startswith("Profile ")):
                    existing.append(path.name)

        if preferred in existing:
            return [preferred]
        if existing:
            return [existing[0]]
        return ["Default"]

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
