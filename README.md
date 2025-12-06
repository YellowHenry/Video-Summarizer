# Video Summarizer

A desktop-friendly demonstration of a video-summarization service. Users can submit local files or YouTube URLs via a Tkinter front-end, while a background worker compresses, summarizes, and persists the generated summaries. The pipeline now attempts real YouTube downloads (via `yt-dlp`), compression (via `ffmpeg` when available), and OpenAI-powered transcription + summarization when provided an API key, while still remaining offline-friendly via fallbacks.

## Features
- Tkinter UI for uploads (file picker or YouTube link) with configurable bitrate slider.
- Background job queue that downloads, compresses, and summarizes videos using a cloud-facing client that speaks to OpenAI/Azure or any compatible endpoint.
- Offline-friendly defaults that simulate downloads and summarization while keeping integration points (HTTP endpoint + API key) ready for a real cloud service.
- Optional real integrations: `yt-dlp` for YouTube downloads, `ffmpeg` for compression, and OpenAI transcription + summarization when `OPENAI_API_KEY` (or `SUMMARIZER_API_KEY`) is set.
- Summaries and compressed artifacts persisted under `storage/` per job ID.

## Architecture
- `app.py` — Tkinter UI that dispatches jobs to the background queue and displays live status updates and summaries.
- `backend/jobs.py` — job model, queue, and worker loop that orchestrate download → compression → summarization.
- `backend/compression.py` — compression config and compressor that prefers `ffmpeg` but degrades to a copy-only fallback.
- `backend/downloader.py` — downloader that attempts `yt-dlp` for YouTube fetches and copies local uploads.
- `backend/summarizer.py` — cloud summarizer client with HTTP integration and OpenAI transcription/summarization support.
- `backend/notifier.py` — optional SMTP email notifications when summaries finish.
- `backend/storage.py` — persistence helper for summaries and compressed copies.

## Running the app
1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Start the Tkinter front-end:
   ```bash
   python app.py
   ```
3. Submit a local video file or YouTube URL. When jobs complete, summaries appear in the UI and under `storage/<job_id>/summary.txt` (with metadata in `summary.json`).

Environment variables for real cloud + notification integrations (set those that apply):

```bash
export OPENAI_API_KEY="sk-..."                  # or SUMMARIZER_API_KEY
export OPENAI_BASE_URL="https://api.openai.com/v1"  # or your Azure endpoint
export OPENAI_ORG="..."                        # optional
export OPENAI_PROJECT="..."                    # optional
export AZURE_OPENAI_API_VERSION="2024-02-15"   # required for Azure
export SUMMARIZER_MODEL="gpt-4o-mini"          # override for Azure deployment names
export SUMMARIZER_HTTP_ENDPOINT="https://example.com/summarize"  # alternative provider
export SMTP_HOST="smtp.sendgrid.net"           # enable email notifications
export SMTP_PORT=587
export SMTP_USER="apikey"
export SMTP_PASSWORD="..."
export SMTP_FROM="summaries@example.com"
```

### Quick smoke test
If you want to verify the pipeline without launching the UI, run the bundled smoke test:

```bash
python smoke_test.py
```

This creates a synthetic media file, runs it through download → compression → summarization, and reports the saved summary path. If
you have `OPENAI_API_KEY` or `SUMMARIZER_HTTP_ENDPOINT` configured, the smoke test uploads a small WAV file so it exercises your
actual cloud provider instead of the local fallback.

### Cloud summarization
Configure one of these options to use real cloud services:

- **OpenAI (recommended):**
  - Export `OPENAI_API_KEY`.
  - Optional: set `OPENAI_BASE_URL` for Azure/OpenAI-compatible endpoints, `OPENAI_ORG`, `OPENAI_PROJECT`, and `AZURE_OPENAI_API_VERSION` when targeting Azure.
  - Optional: override the chat model via `SUMMARIZER_MODEL` (defaults to `gpt-4o-mini`) or the transcription model via `SUMMARIZER_TRANSCRIBE_MODEL` (defaults to `whisper-1`).
- **Custom HTTP endpoint:**
  - Export `SUMMARIZER_HTTP_ENDPOINT` (and `SUMMARIZER_API_KEY` if your endpoint requires it). The app will POST a multipart form with the video attached as `file` and the `model` field set from `SUMMARIZER_MODEL` to that URL. The endpoint must respond with JSON containing a `summary` key.

### Notifications
- Optional email alerts can be enabled by setting `SMTP_HOST` and `SMTP_FROM`. Provide `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_PORT` if your relay requires authentication. When present, the service emails the requester with the summary text once a job finishes so they can review results without reopening the app.

If neither option is configured, the summarizer falls back to a fast local stub so the rest of the workflow still functions.

## Notes
- Compression uses `ffmpeg` when available. If `ffmpeg` is missing, the compressor falls back to a copy-only behavior.
- The YouTube downloader prefers `yt-dlp` (if installed) and falls back to an offline placeholder file to keep the demo working everywhere.
- Set `OPENAI_API_KEY` (or `SUMMARIZER_API_KEY`) to enable OpenAI transcription ("whisper-1") + summarization using `model` from `SummarizerConfig` (defaults to `gpt-4o-mini`). If unset, the app will produce a local placeholder summary to keep the UI responsive.
