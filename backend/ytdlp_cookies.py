import base64
import os
import tempfile
from pathlib import Path
from typing import Optional


def resolve_cookies_file() -> Optional[str]:
    """
    Resolve yt-dlp cookies file path from environment.

    Priority:
      1) YTDLP_COOKIES (path to Netscape cookies.txt)
      2) YTDLP_COOKIES_TEXT (raw cookies.txt contents)
      3) YTDLP_COOKIES_B64 (base64-encoded cookies.txt contents)
    """
    direct_path = (os.getenv("YTDLP_COOKIES") or "").strip()
    if direct_path:
        return direct_path

    cookies_text = os.getenv("YTDLP_COOKIES_TEXT")
    cookies_b64 = os.getenv("YTDLP_COOKIES_B64")
    if not cookies_text and cookies_b64:
        try:
            cookies_text = base64.b64decode(cookies_b64).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None

    if not cookies_text:
        return None

    target_dir = Path(tempfile.gettempdir()) / "capstone_ytdlp"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "cookies.txt"
    target_file.write_text(cookies_text, encoding="utf-8")
    return str(target_file)
