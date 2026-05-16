# YouLearn Demo Script

Use this for a 3-5 minute reviewer walkthrough.

## 1. Hook

YouLearn helps people cut through long YouTube videos. Paste a YouTube URL, let the worker pull captions or audio, and get a private AI summary, transcript, searchable archive, job chat, and optional email digests tied to the signed-in Google user.

## 2. Architecture

- React frontend with Google Sign-In.
- FastAPI API with owner-scoped jobs, artifacts, chat, search, uploads, and digest settings.
- Redis/RQ worker pipeline for `queued -> downloading -> preprocessing -> summarizing -> complete`.
- Cloud SQL stores job metadata, chat history, digest settings, and vector/search metadata.
- GCS stores generated summaries/transcripts and uploaded artifacts.
- OpenAI powers summaries, job chat, global search answers, and digest/profile copy.

## 3. Live Demo Flow

1. Sign in with Google and show that the job list is private to the current account.
2. Paste a YouTube URL and click `Submit`.
3. Point out the Job Detail processing timeline:
   - queued
   - downloading
   - preprocessing
   - summarizing
   - ready
4. Open a completed job and show the summary-first layout.
5. Switch to Transcript and Job Chat.
6. Ask a question about the video in Job Chat.
7. Open Global Search and ask a question across saved transcripts.
8. Show Email Digests and explain daily/weekly recap plus rolling taste profile.

## 4. Failure Demo Talking Point

If a YouTube job fails, the app now shows a plain-English explanation and keeps the raw yt-dlp/provider error inside Debug info. The `Try again` button resubmits the same source URL without making the user copy/paste.

## 5. Built With Codex

Codex was used to iterate across the full stack: FastAPI endpoints, RQ worker behavior, Cloud Run/VM deployment scripts, Google OAuth, GCS artifact access, digest scheduling, Playwright screenshot tests, and UI polish.

## 6. Likely Reviewer Questions

- **Why captions first?** It is faster and cheaper than Whisper when YouTube captions exist.
- **How is privacy handled?** Every job is tied to `owner_email`; API reads require Google auth and owner checks.
- **What happens when YouTube blocks a download?** The worker records a failed job, the UI explains it, and Debug info preserves the technical detail.
- **How do digests avoid spam?** Digests are opt-in, scheduled, and skip empty windows.
- **How is cost controlled?** Redis runs on the worker VM, Cloud SQL is shared-core, and Cloud Run scales down when idle.
