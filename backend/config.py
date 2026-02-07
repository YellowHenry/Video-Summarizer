"""Shared configuration for the audio summarizer."""

from typing import Optional

# Optional hardcoded OpenAI API key. Prefer environment variables in production.
HARDCODED_API_KEY: Optional[str] = "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" 

# Optional hardcoded browser name for yt-dlp cookies (e.g., "chrome", "edge", "firefox").
# If set, downloader/summarizer will use this when YTDLP_COOKIES_FROM_BROWSER is unset.
HARDCODED_COOKIES_FROM_BROWSER: Optional[str] = 'chrome'

# Optional hardcoded browser profile name for yt-dlp cookies (e.g., "Default", "Profile 1").
# Used when YTDLP_COOKIES_FROM_BROWSER_PROFILE is unset.
HARDCODED_COOKIES_BROWSER_PROFILE: Optional[str] = "Default"
