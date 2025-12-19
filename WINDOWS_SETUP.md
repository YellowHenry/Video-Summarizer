# How to Use the Audio Summarizer on Windows

This app ingests audio (local files or YouTube URLs) and can accept local video files by extracting their audio; it transcribes with Whisper and summarizes with a chat model. Video frames are not used.

## Setup
1. Open PowerShell and go to the project folder:
   ```powershell
   cd C:\Users\danmc\Documents\capstone
   ```
2. Install Python packages:
   ```powershell
   pip install -r requirements.txt
   ```
3. Install ffmpeg (needed for audio prep/segmentation). If `ffmpeg -version` works, you’re set. Otherwise install via winget/choco/scoop or set `FFMPEG_PATH` to the ffmpeg.exe path.
4. Set your OpenAI API key in the same PowerShell session for real summaries:
   ```powershell
   $env:OPENAI_API_KEY = "sk-..."
   ```
   (Skip this for the local stub summary.)
5. Run the app:
   ```powershell
   python app.py
   ```

## Using the app
- Provide a local audio file or a YouTube URL, optional email, then click Submit.
- Jobs move through downloading → preprocessing → summarizing → complete.
- Summaries are saved under `storage/<job_id>/summary.txt` (and `summary.json`), alongside the processed audio sent to Whisper (`.whisper_*.m4a` or `.whisper.wav`). 

## Common issues
- **yt-dlp errors**: ensure `yt-dlp` is installed (`pip install -U yt-dlp`) and, for some YouTube formats, install Node.js to satisfy extractor requirements.
- **Whisper timeouts/limits**: long audio is auto-segmented (~42 min chunks) and bitrate-reduced to stay under the 25 MB Whisper cap. Increase `SUMMARIZER_TIMEOUT` if needed.
- **Email not sent**: set `SMTP_HOST` and `SMTP_FROM` (and auth if required) to enable notifications.

## Quick test without UI
```powershell
python smoke_test.py
```
This creates a short WAV, runs it through the pipeline, and prints where the summary was stored.***
