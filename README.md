# Video Summarizer

A desktop-friendly demonstration of a video-summarization service. Users can submit local files or YouTube URLs via a Tkinter front-end, while a background worker compresses, summarizes, and persists the generated summaries. The pipeline now attempts real YouTube downloads (via `yt-dlp`), compression (via `ffmpeg` when available), and OpenAI-powered transcription + summarization when provided an API key, while still remaining offline-friendly via fallbacks.

## Features
- Tkinter UI for uploads (file picker or YouTube link) with configurable bitrate slider.
- Background job queue that downloads, compresses, and summarizes videos using a cloud-facing client stub.
- Offline-friendly defaults that simulate downloads and summarization while keeping integration points (HTTP endpoint + API key) ready for a real cloud service.
- Optional real integrations: `yt-dlp` for YouTube downloads, `ffmpeg` for compression, and OpenAI transcription + summarization when `SUMMARIZER_API_KEY` is set.
- Summaries and compressed artifacts persisted under `storage/` per job ID.

## Architecture
- `app.py` — Tkinter UI that dispatches jobs to the background queue and displays live status updates and summaries.
- `backend/jobs.py` — job model, queue, and worker loop that orchestrate download → compression → summarization.
- `backend/compression.py` — compression config and compressor that prefers `ffmpeg` but degrades to a copy-only fallback.
- `backend/downloader.py` — downloader that attempts `yt-dlp` for YouTube fetches and copies local uploads.
- `backend/summarizer.py` — cloud summarizer client with HTTP integration and OpenAI transcription/summarization support.
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

### Cloud summarization
Set `SUMMARIZER_API_KEY` and update `SummarizerConfig.endpoint` if you want to call a real cloud model. Use `CloudSummarizerClient.summarize_via_http` as a template for posting raw video bytes to your provider.

## Notes
- Compression uses `ffmpeg` when available. If `ffmpeg` is missing, the compressor falls back to a copy-only behavior.
- The YouTube downloader prefers `yt-dlp` (if installed) and falls back to an offline placeholder file to keep the demo working everywhere.
- Set `SUMMARIZER_API_KEY` to enable OpenAI transcription ("whisper-1") + summarization using `model` from `SummarizerConfig` (defaults to `long-video-1.0`). If unset, the app will produce a local placeholder summary to keep the UI responsive.
