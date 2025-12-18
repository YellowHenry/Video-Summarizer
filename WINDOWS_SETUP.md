# How to Use This Video Summarizer on Windows (Simple Guide)

## What This App Does
This app takes videos (either files on your computer or YouTube links) and creates summaries of them using OpenAI's AI. It's like having a smart assistant that watches videos and tells you what they're about!

## What OpenAI Models It Uses
- **For transcribing speech to text**: `whisper-1` (this listens to the video and writes down what people say)
- **For creating summaries**: `gpt-4o-mini` (this reads the transcript and makes a short summary)

You can change these by setting environment variables (see below), but these are the defaults.

---

## Step-by-Step Setup (Like You Just Cloned the Repo)

### Step 1: Make Sure You Have Python
1. Open PowerShell (search for "PowerShell" in Windows Start menu)
2. Type: `python --version`
3. If you see a version number (like "Python 3.11.5"), you're good! ✅
4. If you see an error, download Python from [python.org](https://www.python.org/downloads/) and install it

### Step 2: Install the Required Packages
1. In PowerShell, go to your project folder:
   ```powershell
   cd C:\Users\danmc\Documents\capstone
   ```
2. Install the packages:
   ```powershell
   pip install -r requirements.txt
   ```
   This installs:
   - `requests` (for talking to websites)
   - `yt-dlp` (for downloading YouTube videos)
   - `openai` (for using OpenAI's AI)

### Step 3: Set Your OpenAI API Key
You have **two options**:

#### Option A: Use the PowerShell Script (Easiest!)
1. Open the file `set_api_key.ps1` in a text editor (like Notepad)
2. Replace `"sk-your-actual-key-here"` with your actual OpenAI API key (it should start with `sk-`)
3. Save the file
4. In PowerShell, run:
   ```powershell
   .\set_api_key.ps1
   ```
   This will set your API key and start the app!

#### Option B: Set It Manually Each Time
1. Open PowerShell
2. Go to your project folder:
   ```powershell
   cd C:\Users\danmc\Documents\capstone
   ```
3. Set your API key (replace `YOUR-ACTUAL-KEY-HERE` with your real key):
   ```powershell
   $env:OPENAI_API_KEY = "YOUR-ACTUAL-KEY-HERE"
   ```
4. Run the app:
   ```powershell
   python app.py
   ```

**Note**: With Option B, you'll need to set the key every time you open a new PowerShell window. Option A is easier!

### Step 4: Run the App!
If you used Option A, the app should already be running. If you used Option B, just type:
```powershell
python app.py
```

A window should pop up! 🎉

---

## How to Use the App

1. **Submit a video**:
   - Click "Browse" to pick a video file from your computer, OR
   - Paste a YouTube URL in the "YouTube URL" box

2. **Optional settings**:
   - Adjust the bitrate slider (lower = smaller file, but might look worse)
   - Add your email if you want to get notified when the summary is done

3. **Click "Submit"**
   - Your job will appear in the "Jobs" table
   - Watch the status change from "pending" → "downloading" → "compressing" → "summarizing" → "complete"

4. **View the summary**:
   - Click on a completed job in the table
   - The summary will appear in the "Summary" panel at the bottom

---

## Troubleshooting

### "I don't have an OpenAI API key!"
1. Go to [platform.openai.com](https://platform.openai.com)
2. Sign up or log in
3. Go to "API keys" section
4. Click "Create new secret key"
5. Copy the key (it starts with `sk-`)
6. **Important**: Save it somewhere safe! You can't see it again after you close the page.

### "The app says 'Cloud summarization was configured but all providers failed'"
- Check that your API key is correct
- Make sure you have internet connection
- Check that your OpenAI account has credits/usage available

### "I want to use a different OpenAI model"
Set this before running the app:
```powershell
$env:SUMMARIZER_MODEL = "gpt-4"  # or whatever model you want
python app.py
```

### "I want to use a different transcription model"
Set this before running the app:
```powershell
$env:SUMMARIZER_TRANSCRIBE_MODEL = "whisper-1"  # or another model
python app.py
```

---

## Quick Test (Without Opening the App)
Want to test if everything works? Run:
```powershell
python smoke_test.py
```
This creates a fake video, processes it, and shows you where the summary was saved.

---

## Summary
1. ✅ Install Python (if needed)
2. ✅ Run `pip install -r requirements.txt`
3. ✅ Set your OpenAI API key (use `set_api_key.ps1` or set `$env:OPENAI_API_KEY`)
4. ✅ Run `python app.py`
5. ✅ Submit a video and get a summary!

**Default models used:**
- Transcription: `whisper-1`
- Summarization: `gpt-4o-mini`

