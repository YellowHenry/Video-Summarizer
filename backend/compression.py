import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class CompressionConfig:
    target_bitrate_kbps: int = 800
    max_resolution: str = "720p"


class Compressor:
    """Simulates video compression to a temporary location."""

    def __init__(self, config: Optional[CompressionConfig] = None):
        self.config = config or CompressionConfig()
        self.logger = logging.getLogger(__name__)

    def compress(self, source: Path) -> Path:
        if not source.exists():
            raise FileNotFoundError(f"Video not found: {source}")

        temp_dir = Path(tempfile.mkdtemp(prefix="compressed_"))
        compressed_path = temp_dir / source.name

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            command = [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-b:v",
                f"{self.config.target_bitrate_kbps}k",
                str(compressed_path),
            ]
            # Respect max resolution if provided
            if self.config.max_resolution.lower() == "720p":
                command.insert(-1, "-vf")
                command.insert(-1, "scale=-1:720")
            try:
                subprocess.run(command, check=True, capture_output=True)
                if compressed_path.exists():
                    return compressed_path
            except subprocess.CalledProcessError:
                self.logger.warning("ffmpeg compression failed for %s; copying instead", source)
            except FileNotFoundError:
                self.logger.warning("ffmpeg not available on PATH; copying instead")

        shutil.copy2(source, compressed_path)
        return compressed_path
