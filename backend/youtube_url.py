from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse


def extract_youtube_video_id(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return None

    host = (parsed.netloc or "").lower()
    if "youtu.be" in host:
        candidate = parsed.path.strip("/")
        return candidate or None

    if "youtube.com" in host or "music.youtube.com" in host:
        query = parse_qs(parsed.query)
        candidate = (query.get("v") or [None])[0]
        if candidate:
            return candidate
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed"}:
            return parts[1]
    return None


def canonicalize_youtube_video_url(url: str) -> str:
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return url
    return f"https://www.youtube.com/watch?v={video_id}"


def unwrap_ytdlp_video_info(info: dict | None, target_video_id: str | None) -> dict | None:
    """
    If yt-dlp returned a playlist wrapper, return the matching entry for the target video id.
    Otherwise return the original info object.
    """
    if not isinstance(info, dict):
        return info

    entries = info.get("entries")
    if entries is None:
        return info
    if not target_video_id:
        return info

    if isinstance(entries, list):
        iterable: Iterable[Any] = entries
    elif isinstance(entries, Iterable):
        iterable = entries
    else:
        return info

    for entry in iterable:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id") or "") == target_video_id:
            return entry
    return info
