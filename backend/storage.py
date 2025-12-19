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

    def store_summary(self, job_id: str, summary_text: str) -> Path:
        summary_dir = self.config.base_dir / job_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        json_path = summary_dir / "summary.json"
        payload = {"job_id": job_id, "summary": summary_text}
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        text_path = summary_dir / "summary.txt"
        text_path.write_text(summary_text, encoding="utf-8")
        return text_path

    def store_compressed_copy(self, job_id: str, media: Path) -> Path:
        summary_dir = self.config.base_dir / job_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        output_path = summary_dir / media.name
        if media.resolve() != output_path.resolve():
            output_path.write_bytes(media.read_bytes())
        return output_path
