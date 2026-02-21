from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse


RATE_LIMIT_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "requestblocked",
    "ipblocked",
    "temporarily unavailable",
    "quota exceeded",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _safe_int(raw: str | None, default: int) -> int:
    try:
        return int((raw or "").strip())
    except Exception:
        return default


def _safe_float(raw: str | None, default: float) -> float:
    try:
        return float((raw or "").strip())
    except Exception:
        return default


def _expand_proxy_template(template: str, start: int, end: int) -> list[str]:
    template = template.strip()
    if not template:
        return []
    if "{i" not in template and "{index" not in template and "{n" not in template:
        return [template]

    step = 1 if end >= start else -1
    items: list[str] = []
    for i in range(start, end + step, step):
        try:
            value = template.format(i=i, index=i, n=i).strip()
        except Exception:
            continue
        if value:
            items.append(value)
    return items


@dataclass(frozen=True)
class ProxyEgressConfig:
    enabled: bool
    rotation_mode: str
    max_retries: int
    backoff_seconds: float
    proxies: tuple[str, ...]


def load_proxy_egress_config(purpose: str | None = None) -> ProxyEgressConfig:
    enabled = _env_bool("PROXY_ENABLED", False)
    captions_only = _env_bool("PROXY_CAPTIONS_ONLY", False)
    purpose_key = (purpose or "").strip().lower()
    if enabled and captions_only and purpose_key not in {"captions"}:
        enabled = False
    rotation_mode = (os.getenv("PROXY_ROTATION_MODE", "on_rate_limit") or "on_rate_limit").strip().lower()
    if rotation_mode not in {"none", "per_job", "on_rate_limit"}:
        rotation_mode = "on_rate_limit"

    max_retries = max(1, _safe_int(os.getenv("PROXY_MAX_RETRIES"), 3 if enabled else 1))
    backoff_seconds = max(0.0, _safe_float(os.getenv("PROXY_BACKOFF_SECONDS"), 2.0))

    pool = _parse_csv(os.getenv("PROXY_POOL"))
    if _env_bool("PROXY_AUTOGENERATE", False):
        template = os.getenv("PROXY_AUTOGENERATE_TEMPLATE", "")
        start = _safe_int(os.getenv("PROXY_AUTOGENERATE_START"), 1)
        end = _safe_int(os.getenv("PROXY_AUTOGENERATE_END"), start)
        pool.extend(_expand_proxy_template(template, start, end))

    candidates = pool + [
        (os.getenv("YTDLP_PROXY") or "").strip(),
        (os.getenv("ALL_PROXY") or "").strip(),
        (os.getenv("HTTPS_PROXY") or "").strip(),
        (os.getenv("HTTP_PROXY") or "").strip(),
    ]
    proxies = tuple(_dedupe([value for value in candidates if value]))

    if not enabled or not proxies:
        return ProxyEgressConfig(
            enabled=False,
            rotation_mode="none",
            max_retries=1,
            backoff_seconds=0.0,
            proxies=(),
        )

    return ProxyEgressConfig(
        enabled=True,
        rotation_mode=rotation_mode,
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        proxies=proxies,
    )


def select_proxy_for_attempt(
    config: ProxyEgressConfig, attempt_index: int, stable_key: str | None = None
) -> str | None:
    if not config.enabled or not config.proxies:
        return None

    size = len(config.proxies)
    if config.rotation_mode == "none":
        idx = 0
    elif config.rotation_mode == "per_job":
        seed = (stable_key or "").encode("utf-8")
        base = int(hashlib.sha256(seed).hexdigest(), 16) % size if seed else 0
        idx = (base + attempt_index) % size
    else:
        idx = attempt_index % size

    return config.proxies[idx]


def proxy_index(config: ProxyEgressConfig, proxy_url: str | None) -> int | None:
    if not proxy_url:
        return None
    try:
        return config.proxies.index(proxy_url) + 1
    except ValueError:
        return None


def redact_proxy(proxy_url: str | None) -> str:
    if not proxy_url:
        return "direct"
    try:
        parsed = urlparse(proxy_url)
        scheme = parsed.scheme or "proxy"
        host = parsed.hostname or "unknown-host"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{scheme}://{host}{port}"
    except Exception:
        return "configured-proxy"


def build_requests_proxies(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def is_rate_limited_error(error_text: str | None) -> bool:
    message = (error_text or "").lower()
    return any(marker in message for marker in RATE_LIMIT_MARKERS)


def sleep_backoff(config: ProxyEgressConfig, attempt_index: int) -> None:
    if config.backoff_seconds <= 0:
        return
    delay = config.backoff_seconds * (2 ** max(0, attempt_index))
    time.sleep(delay)
