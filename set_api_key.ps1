# Set your OpenAI API key here
# Replace "sk-your-actual-key-here" with your real OpenAI API key
# You can get one from https://platform.openai.com/api-keys

$env:OPENAI_API_KEY = "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"

# Optional: Change the model used for summarization (default is "gpt-4o-mini")
# $env:SUMMARIZER_MODEL = "gpt-4"

# Optional: Change the transcription model (default is "whisper-1")
# $env:SUMMARIZER_TRANSCRIBE_MODEL = "whisper-1"

# Run the app
Write-Host "Starting Audio Summarizer app..." -ForegroundColor Green
Write-Host "Your OpenAI API key is set. The app will use:" -ForegroundColor Yellow
$transcribeModel = if ($env:SUMMARIZER_TRANSCRIBE_MODEL) { $env:SUMMARIZER_TRANSCRIBE_MODEL } else { "whisper-1" }
$summarizeModel = if ($env:SUMMARIZER_MODEL) { $env:SUMMARIZER_MODEL } else { "gpt-4o-mini" }
Write-Host "  - Transcription model: $transcribeModel" -ForegroundColor Cyan
Write-Host "  - Summarization model: $summarizeModel" -ForegroundColor Cyan
Write-Host ""
python app.py
