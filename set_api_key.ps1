# Local-only helper. Do not commit real API keys.
# Prefer setting OPENAI_API_KEY in your shell profile, a local .env file, or
# Secret Manager for deployed services.
if (-not $env:OPENAI_API_KEY) {
  Write-Host "Set OPENAI_API_KEY before running this helper." -ForegroundColor Yellow
  Write-Host 'Example: $env:OPENAI_API_KEY = "replace-with-openai-key"' -ForegroundColor Yellow
  exit 1
}

# Optional: Change the model used for summarization (default is "gpt-4o-mini")
# $env:SUMMARIZER_MODEL = "gpt-4"

# Optional: Change the transcription model (default is "whisper-1")
# $env:SUMMARIZER_TRANSCRIBE_MODEL = "whisper-1"

# Run the app
Write-Host "Starting Audio Summarizer app..." -ForegroundColor Green
Write-Host "OPENAI_API_KEY is set in this shell. The app will use:" -ForegroundColor Yellow
$transcribeModel = if ($env:SUMMARIZER_TRANSCRIBE_MODEL) { $env:SUMMARIZER_TRANSCRIBE_MODEL } else { "whisper-1" }
$summarizeModel = if ($env:SUMMARIZER_MODEL) { $env:SUMMARIZER_MODEL } else { "gpt-4o-mini" }
Write-Host "  - Transcription model: $transcribeModel" -ForegroundColor Cyan
Write-Host "  - Summarization model: $summarizeModel" -ForegroundColor Cyan
Write-Host ""
python app.py
