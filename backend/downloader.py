import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from .config import HARDCODED_COOKIES_FROM_BROWSER, HARDCODED_COOKIES_BROWSER_PROFILE
from .proxy_egress import (
    is_rate_limited_error,
    load_proxy_egress_config,
    proxy_index,
    redact_proxy,
    select_proxy_for_attempt,
    sleep_backoff,
)
from .ytdlp_cookies import resolve_cookies_file

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
PATH_FALLBACK = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Default to strict cookie-auth mode so web/worker behaves like desktop cookie usage.
STRICT_COOKIES = _env_bool("YTDLP_STRICT_COOKIES", True)

COOKIES_FILE = resolve_cookies_file()  # optional path to cookies.txt (Netscape format)
COOKIES_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER")
if COOKIES_BROWSER is None:
    # In Cloud Run there is no local desktop browser profile to read cookies from.
    # Strict mode will require a cookies file/proxy configuration in that case.
    COOKIES_BROWSER = None if os.getenv("K_SERVICE") else HARDCODED_COOKIES_FROM_BROWSER
COOKIES_BROWSER_PROFILE = os.getenv("YTDLP_COOKIES_FROM_BROWSER_PROFILE") or HARDCODED_COOKIES_BROWSER_PROFILE


def _parse_csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


JS_RUNTIMES = _parse_csv_env("YTDLP_JS_RUNTIMES", "node")
REMOTE_COMPONENTS = _parse_csv_env("YTDLP_REMOTE_COMPONENTS", "")


def _js_runtime_dict() -> dict[str, dict]:
    return {runtime: {} for runtime in JS_RUNTIMES}


def _extra_cli_flags() -> list[str]:
    flags: list[str] = []
    for runtime in JS_RUNTIMES:
        flags += ["--js-runtimes", runtime]
    for component in REMOTE_COMPONENTS:
        flags += ["--remote-components", component]
    return flags


def _ensure_runtime_path() -> None:
    if not (os.getenv("PATH") or "").strip():
        os.environ["PATH"] = PATH_FALLBACK


def _chrome_user_data_root() -> Path:
    """Return Chrome profile root for the current OS."""
    local_app_data = (os.getenv("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data) / "Google" / "Chrome" / "User Data"

    home = Path.home()
    candidates = [
        home / ".config" / "google-chrome",
        home / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome",
        home / ".config" / "chromium",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


CHROME_USER_DATA = _chrome_user_data_root()


class AudioDownloader:
    def __init__(self, download_root: Optional[Path] = None):
        self.download_root = download_root or Path(tempfile.gettempdir())
        self.proxy_config = load_proxy_egress_config(purpose="audio_download")
        self.logger = logging.getLogger(__name__)

    def get_youtube_title(self, url: str) -> Optional[str]:
        """
        Best-effort title lookup without downloading media.
        Never raises, so callers can use it opportunistically.
        """
        try:
            return self._get_title(url)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("YouTube title lookup failed for %s: %s", url, exc)
            return None

    def _cookie_attempts(self) -> list[tuple[list[str], str]]:
        """
        Build cookie strategies to try.
        In strict mode, only authenticated cookie attempts are allowed.
        """
        attempts: list[tuple[list[str], str]] = []
        if COOKIES_BROWSER:
            for profile in self._candidate_profiles():
                attempts.append(
                    (
                        ["--cookies-from-browser", f"{COOKIES_BROWSER}:{profile}"],
                        f"browser:{COOKIES_BROWSER} profile:{profile}",
                    )
                )
        if COOKIES_FILE:
            attempts.append((["--cookies", COOKIES_FILE], f"file:{COOKIES_FILE}"))
        if not attempts and STRICT_COOKIES:
            raise RuntimeError(
                "YouTube cookie auth is required (YTDLP_STRICT_COOKIES=true), but no cookies were configured. "
                "Set YTDLP_COOKIES_FILE/YTDLP_COOKIES_B64/YTDLP_COOKIES, or disable strict mode explicitly."
            )
        if not STRICT_COOKIES:
            attempts.append(([], "none"))
        return attempts

    def _get_title(self, url: str) -> Optional[str]:
        """Fetch the YouTube title without downloading media."""
        _ensure_runtime_path()
        try:
            import yt_dlp
        except ImportError:
            return None

        attempts = self.proxy_config.max_retries if self.proxy_config.enabled else 1
        attempts = max(1, attempts)
        for attempt_idx in range(attempts):
            proxy_url = select_proxy_for_attempt(self.proxy_config, attempt_idx, stable_key=url)
            proxy_slot = proxy_index(self.proxy_config, proxy_url)
            proxy_label = redact_proxy(proxy_url)
            last_error: Optional[str] = None

            for extractor_arg in FALLBACK_EXTRACTOR_ARGS:
                for cookies_flags, cookies_label in self._cookie_attempts():
                    ydl_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "skip_download": True,
                        "extract_flat": False,
                        "extractor_args": {"youtube": [extractor_arg]},
                    }
                    if JS_RUNTIMES:
                        ydl_opts["js_runtimes"] = _js_runtime_dict()
                    if REMOTE_COMPONENTS:
                        ydl_opts["remote_components"] = REMOTE_COMPONENTS
                    if proxy_url:
                        ydl_opts["proxy"] = proxy_url
                    if cookies_flags and cookies_flags[0] == "--cookies-from-browser":
                        browser_spec = cookies_flags[1]
                        browser, profile = browser_spec.split(":", 1)
                        ydl_opts["cookiesfrombrowser"] = (browser, profile, None, None)
                    elif cookies_flags and cookies_flags[0] == "--cookies":
                        ydl_opts["cookiefile"] = cookies_flags[1]
                    try:
                        self.logger.info(
                            "yt-dlp title attempt extractor=%s cookies=%s egress=%s proxy_slot=%s attempt=%s/%s",
                            extractor_arg,
                            cookies_label,
                            proxy_label,
                            proxy_slot or "n/a",
                            attempt_idx + 1,
                            attempts,
                        )
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            info = ydl.extract_info(url, download=False)
                            if info and info.get("title"):
                                return info["title"]
                    except Exception as exc:  # noqa: BLE001
                        last_error = str(exc)
                        self.logger.debug(
                            "Title fetch failed extractor=%s cookies=%s proxy_slot=%s error=%s",
                            extractor_arg,
                            cookies_label,
                            proxy_slot or "n/a",
                            exc,
                        )
                        continue

            if is_rate_limited_error(last_error) and attempt_idx + 1 < attempts:
                self.logger.warning(
                    "yt-dlp title lookup rate-limited (proxy_slot=%s attempt=%s/%s). Rotating.",
                    proxy_slot or "n/a",
                    attempt_idx + 1,
                    attempts,
                )
                sleep_backoff(self.proxy_config, attempt_idx)
                continue
            break
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

        _ensure_runtime_path()
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
            base_common += _extra_cli_flags()
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
            base_common += _extra_cli_flags()
        attempt_matrix = []
        for extractor_arg in FALLBACK_EXTRACTOR_ARGS:
            for cookies_flags, cookies_label in self._cookie_attempts():
                attempt_matrix.append((extractor_arg, cookies_label, cookies_flags))

        proxy_attempts = self.proxy_config.max_retries if self.proxy_config.enabled else 1
        proxy_attempts = max(1, proxy_attempts)
        last_error: Optional[str] = None
        for attempt_idx in range(proxy_attempts):
            proxy_url = select_proxy_for_attempt(self.proxy_config, attempt_idx, stable_key=url)
            proxy_slot = proxy_index(self.proxy_config, proxy_url)
            proxy_label = redact_proxy(proxy_url)
            rate_limited_this_round = False

            for extractor_arg, cookies_label, cookies_flags in attempt_matrix:
                proxy_flags = ["--proxy", proxy_url] if proxy_url else []
                attempt = base_common + ["--extractor-args", extractor_arg] + cookies_flags + proxy_flags + [url]
                self.logger.info(
                    "yt-dlp attempt extractor=%s cookies=%s egress=%s proxy_slot=%s attempt=%s/%s",
                    extractor_arg,
                    cookies_label,
                    proxy_label,
                    proxy_slot or "n/a",
                    attempt_idx + 1,
                    proxy_attempts,
                )
                try:
                    run_env = os.environ.copy()
                    if not (run_env.get("PATH") or "").strip():
                        run_env["PATH"] = PATH_FALLBACK
                    result = subprocess.run(attempt, check=True, capture_output=True, text=True, env=run_env)
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
                    if is_rate_limited_error(err_text):
                        rate_limited_this_round = True
                    continue

            if rate_limited_this_round and attempt_idx + 1 < proxy_attempts:
                self.logger.warning(
                    "yt-dlp download rate-limited (proxy_slot=%s attempt=%s/%s). Rotating.",
                    proxy_slot or "n/a",
                    attempt_idx + 1,
                    proxy_attempts,
                )
                sleep_backoff(self.proxy_config, attempt_idx)
                continue
            break

        error_log = temp_dir / "download_error.log"
        if last_error:
            error_log.write_text(last_error, encoding="utf-8")
        hint = ""
        if "403" in (last_error or "") or "SABR" in (last_error or ""):
            hint = " Hint: this video may require cookies/login; set YTDLP_COOKIES to a cookies.txt path or try another client."
        if "sign in to confirm you" in (last_error or "").lower():
            hint += (
                " Hint: YouTube blocked this cloud request. Configure YTDLP_COOKIES "
                "(or YTDLP_COOKIES_TEXT/YTDLP_COOKIES_B64), use a proxy, or upload the media file directly."
            )
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
