from __future__ import annotations

import json
import logging
from functools import lru_cache
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)


def looks_like_url(value: str | None) -> bool:
    if not value:
        return False
    trimmed = value.strip().lower()
    return trimmed.startswith("http://") or trimmed.startswith("https://") or trimmed.startswith("www.")


def extract_youtube_video_id(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().replace("www.", "")
        if host == "youtu.be":
            parts = [part for part in parsed.path.split("/") if part]
            return parts[0] if parts else None
        if host in {"youtube.com", "m.youtube.com"}:
            if parsed.path == "/watch":
                query = parsed.query
                for piece in query.split("&"):
                    if piece.startswith("v="):
                        value = piece[2:]
                        return value or None
                return None
            if parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
                parts = [part for part in parsed.path.split("/") if part]
                return parts[1] if len(parts) > 1 else None
        return None
    except Exception:
        return None


def canonicalize_youtube_video_url(url: str | None) -> str | None:
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return None
    return f"https://www.youtube.com/watch?v={video_id}"


@lru_cache(maxsize=512)
def _fetch_youtube_oembed_title_cached(canonical_url: str) -> str | None:
    endpoint = "https://www.youtube.com/oembed?" + urlencode({"url": canonical_url, "format": "json"})
    request = Request(
        endpoint,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            )
        },
    )
    try:
        with urlopen(request, timeout=2.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            title = payload.get("title")
            if isinstance(title, str):
                normalized = title.strip()
                if normalized:
                    return normalized
    except Exception as exc:  # noqa: BLE001
        logger.debug("YouTube oEmbed title lookup failed for %s: %s", canonical_url, exc)
    return None


def fetch_youtube_oembed_title(url: str, timeout_seconds: float = 2.5) -> str | None:
    canonical = canonicalize_youtube_video_url(url)
    if not canonical:
        return None
    # timeout_seconds is retained for interface compatibility; cache key uses canonical URL.
    _ = timeout_seconds
    return _fetch_youtube_oembed_title_cached(canonical)


def display_title_fallback(source_url: str | None) -> str:
    video_id = extract_youtube_video_id(source_url)
    if video_id:
        return f"YouTube video ({video_id})"
    if source_url and source_url.strip():
        return source_url.strip()
    return "(untitled job)"


def resolve_display_title(
    stored_title: str | None,
    source_url: str | None,
    *,
    allow_oembed: bool,
) -> tuple[str, str]:
    if stored_title and stored_title.strip() and not looks_like_url(stored_title):
        return stored_title.strip(), "stored_title"

    if allow_oembed and source_url:
        oembed_title = fetch_youtube_oembed_title(source_url)
        if oembed_title:
            return oembed_title, "oembed"

    return display_title_fallback(source_url), "fallback"
