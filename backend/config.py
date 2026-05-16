"""Shared configuration for the audio summarizer."""

import os
from typing import Optional

# Optional local fallback for development only.
# Prefer OPENAI_API_KEY from the environment or Secret Manager-backed deploys.
OPENAI_API_KEY: Optional[str] = None

# Backward-compat alias used in older modules.
HARDCODED_API_KEY: Optional[str] = OPENAI_API_KEY


def get_openai_api_key() -> Optional[str]:
    """
    Resolve OpenAI API key without requiring secrets in source control.
    Priority:
      1) OPENAI_API_KEY environment variable
      2) SUMMARIZER_API_KEY environment variable
      3) backend/config.py OPENAI_API_KEY local fallback
    """
    from_env = (os.getenv("OPENAI_API_KEY") or "").strip()
    if from_env:
        return from_env
    from_summarizer_env = (os.getenv("SUMMARIZER_API_KEY") or "").strip()
    if from_summarizer_env:
        return from_summarizer_env
    from_config = (OPENAI_API_KEY or "").strip()
    return from_config or None

# Optional hardcoded browser name for yt-dlp cookies (e.g., "chrome", "edge", "firefox").
# If set, downloader/summarizer will use this when YTDLP_COOKIES_FROM_BROWSER is unset.
HARDCODED_COOKIES_FROM_BROWSER: Optional[str] = 'chrome'

# Optional hardcoded browser profile name for yt-dlp cookies (e.g., "Default", "Profile 1").
# Used when YTDLP_COOKIES_FROM_BROWSER_PROFILE is unset.
HARDCODED_COOKIES_BROWSER_PROFILE: Optional[str] = "Default"
