import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class VideoDownloader:
    def __init__(self, download_root: Optional[Path] = None):
        self.download_root = download_root or Path(tempfile.gettempdir())

    def download_youtube(self, url: str) -> Path:
        """Attempt to download a YouTube video using ``yt-dlp``.

        Falls back to generating a placeholder file if ``yt-dlp`` is not
        available or the download fails. This keeps the demo usable offline
        while still providing a real implementation path.
        """

        sanitized = url.replace("https://", "").replace("http://", "")
        filename = sanitized.replace("/", "_")[:80] or "youtube_video"
        temp_dir = Path(tempfile.mkdtemp(prefix="yt_", dir=self.download_root))
        output_path = temp_dir / f"{filename}.mp4"

        if shutil.which("yt-dlp"):
            command = [
                "yt-dlp",
                "-o",
                str(output_path),
                "-f",
                "mp4",
                url,
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
                if output_path.exists():
                    return output_path
            except subprocess.CalledProcessError as exc:  # noqa: PERF203
                error_log = temp_dir / "download_error.log"
                error_log.write_text(exc.stderr or exc.stdout or str(exc), encoding="utf-8")

        # Offline-friendly fallback
        output_path.write_text("synthetic video bytes", encoding="utf-8")
        return output_path

    def copy_local(self, source: Path) -> Path:
        if not source.exists():
            raise FileNotFoundError(f"Local video not found: {source}")
        temp_dir = Path(tempfile.mkdtemp(prefix="upload_", dir=self.download_root))
        destination = temp_dir / source.name
        shutil.copy2(source, destination)
        return destination
