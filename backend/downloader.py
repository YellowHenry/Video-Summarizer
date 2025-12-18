import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class VideoDownloader:
    def __init__(self, download_root: Optional[Path] = None):
        self.download_root = download_root or Path(tempfile.gettempdir())
        self.logger = logging.getLogger(__name__)

    def download_youtube(self, url: str) -> Path:
        """Attempt to download a YouTube video using ``yt-dlp``.

        Raises RuntimeError if ``yt-dlp`` is not available, as it's required
        for real YouTube downloads.
        """

        sanitized = url.replace("https://", "").replace("http://", "")
        # Remove all invalid Windows filename characters: < > : " / \ | ? *
        invalid_chars = '<>:"/\\|?*'
        filename = sanitized
        for char in invalid_chars:
            filename = filename.replace(char, "_")
        filename = filename[:80] or "youtube_video"
        temp_dir = Path(tempfile.mkdtemp(prefix="yt_", dir=self.download_root))
        output_path = temp_dir / f"{filename}.mp4"

        # Try to find yt-dlp executable first, then fall back to python -m yt_dlp
        yt_dlp = shutil.which("yt-dlp")
        if yt_dlp:
            command = [
                yt_dlp,
                "-o",
                str(output_path),
                "-f",
                "mp4",
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
                "mp4",
                url,
            ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            if output_path.exists():
                return output_path
            # If yt-dlp succeeded but file doesn't exist, check for alternative output
            # yt-dlp might have created a file with a different name/extension
            temp_files = list(temp_dir.glob("*"))
            if temp_files:
                # Return the first non-log file found
                for f in temp_files:
                    if f.suffix in (".mp4", ".webm", ".mkv", ".m4a", ".mp3") and f.name != "download_error.log":
                        return f
            raise RuntimeError(f"yt-dlp completed but no video file was created. Output: {result.stdout}")
        except subprocess.CalledProcessError as exc:  # noqa: PERF203
            error_log = temp_dir / "download_error.log"
            error_msg = exc.stderr or exc.stdout or str(exc)
            error_log.write_text(error_msg, encoding="utf-8")
            raise RuntimeError(
                f"yt-dlp failed to download {url}. Error: {error_msg}"
            ) from exc

    def copy_local(self, source: Path) -> Path:
        if not source.exists():
            raise FileNotFoundError(f"Local video not found: {source}")
        temp_dir = Path(tempfile.mkdtemp(prefix="upload_", dir=self.download_root))
        destination = temp_dir / source.name
        shutil.copy2(source, destination)
        return destination
