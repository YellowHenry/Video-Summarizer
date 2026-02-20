"""Shared configuration for the audio summarizer."""

import os
from typing import Optional

# Single-source OpenAI API key for this repository.
# Put your key here if you want code and helper scripts to pick it up automatically.
# In production, environment variables and secret managers are preferred.
# Do not commit real keys to a public repository.
OPENAI_API_KEY: Optional[str] = "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"

# Backward-compat alias used in older modules.
HARDCODED_API_KEY: Optional[str] = OPENAI_API_KEY


def get_openai_api_key() -> Optional[str]:
    """
    Resolve OpenAI API key from a single repository source with env fallback.
    Priority:
      1) backend/config.py OPENAI_API_KEY
      2) OPENAI_API_KEY environment variable
      3) SUMMARIZER_API_KEY environment variable
    """
    from_config = (OPENAI_API_KEY or "").strip()
    if from_config:
        return from_config
    from_env = (os.getenv("OPENAI_API_KEY") or "").strip()
    if from_env:
        return from_env
    from_summarizer_env = (os.getenv("SUMMARIZER_API_KEY") or "").strip()
    return from_summarizer_env or None

# Optional hardcoded browser name for yt-dlp cookies (e.g., "chrome", "edge", "firefox").
# If set, downloader/summarizer will use this when YTDLP_COOKIES_FROM_BROWSER is unset.
HARDCODED_COOKIES_FROM_BROWSER: Optional[str] = 'chrome'

# Optional hardcoded browser profile name for yt-dlp cookies (e.g., "Default", "Profile 1").
# Used when YTDLP_COOKIES_FROM_BROWSER_PROFILE is unset.
HARDCODED_COOKIES_BROWSER_PROFILE: Optional[str] = "Default"
