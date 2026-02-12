# Audio Summarizer

A desktop-friendly audio summarization demo. Users submit local audio (or video—audio is extracted) files or YouTube URLs; a background worker downloads/ingests the audio, prepares it with ffmpeg, sends it to Whisper for transcription, and summarizes the transcript with a chat model. If no cloud settings are provided, it falls back to a fast local stub summary.

## Features
- Tkinter UI for uploads (audio file picker or YouTube link).
- Background job queue that downloads audio (yt-dlp), prepares/segments it (ffmpeg), transcribes (Whisper), and summarizes (chat completions).
- Offline-friendly defaults: produces a local placeholder summary when no API key/endpoint is set.
- Persists summaries and artifacts under `storage/<job_id>/`.

## Architecture
- `app.py` — Tkinter UI and job/event handling.
- `backend/jobs.py` — job model, queue, and worker loop.
- `backend/downloader.py` — YouTube audio download and local copy helper.
- `backend/compression.py` — optional ffmpeg-based copy/recompression (noop for audio-only inputs).
- `backend/summarizer.py` — audio prep, chunking, transcription (Whisper), and summarization.
- `backend/storage.py` — persistence for summaries and stored inputs.
- `backend/notifier.py` — optional SMTP notifications.

## Running the app
1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   ```
2. Set your OpenAI key (for real summaries):  
   PowerShell example: `$env:OPENAI_API_KEY = "sk-..."`
3. Start the UI:
   ```bash
   python app.py
   ```
4. Submit a local audio/video file or YouTube URL. When jobs complete, summaries appear in the UI and under `storage/<job_id>/summary.txt` (plus `summary.json`).

Environment variables for real cloud + notifications (set those that apply):
```bash
export OPENAI_API_KEY="sk-..."                  # or SUMMARIZER_API_KEY
export OPENAI_BASE_URL="https://api.openai.com/v1"  # or Azure-compatible endpoint
export OPENAI_ORG="..."                        # optional
export OPENAI_PROJECT="..."                    # optional
export AZURE_OPENAI_API_VERSION="2024-02-15"   # required for Azure
export SUMMARIZER_MODEL="gpt-4o-mini"          # chat model override
export SUMMARIZER_MAX_TOKENS=800               # chat completion cap
export SUMMARIZER_CHUNK_SECONDS=2520           # chunk audio for Whisper (default 42 minutes)
export SUMMARIZER_HTTP_ENDPOINT="https://example.com/summarize"  # alternative provider
export FFMPEG_PATH="/usr/local/bin/ffmpeg"     # set if ffmpeg is not on PATH
export YTDLP_EXTRACTOR_ARGS="youtube:player_client=android"  # override YouTube client if downloads 403
export YTDLP_COOKIES="C:/path/to/cookies.txt"  # optional cookies for protected videos
export YTDLP_COOKIES_FROM_BROWSER="chrome"     # optional: pull cookies directly from browser (yt-dlp supports many)
export YTDLP_COOKIES_FROM_BROWSER_PROFILE="Default"  # optional: browser profile name (Default, Profile 1, etc.)
export SMTP_HOST="smtp.sendgrid.net"           # enable email notifications
export SMTP_PORT=587
export SMTP_USER="apikey"
export SMTP_PASSWORD="..."
export SMTP_FROM="summaries@example.com"
```

### Notes
- Audio prep uses `ffmpeg` when available. It shrinks/segments audio for Whisper; set `FFMPEG_PATH` if it’s not on PATH.
- The exact audio sent to Whisper is saved alongside the stored input as `<original>.whisper_<bitrate>k.m4a` (or `.whisper.wav` fallback).
- The YouTube downloader pulls audio-only (`yt-dlp`) to minimize size.
