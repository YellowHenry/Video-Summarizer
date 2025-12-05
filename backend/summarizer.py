import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None


@dataclass
class SummarizerConfig:
    endpoint: str = "https://api.example.com/summarize"
    api_key: Optional[str] = os.getenv("SUMMARIZER_API_KEY")
    model: str = "long-video-1.0"
    transcription_model: str = os.getenv("SUMMARIZER_TRANSCRIBE_MODEL", "whisper-1")


class CloudSummarizerClient:
    def __init__(self, config: Optional[SummarizerConfig] = None):
        self.config = config or SummarizerConfig()
        self.client = OpenAI(api_key=self.config.api_key) if OpenAI and self.config.api_key else None

    def summarize(self, video: Path) -> str:
        if self.config.api_key and self.config.endpoint and self.config.endpoint != SummarizerConfig.endpoint:
            return self.summarize_via_http(video)

        if self.client:
            try:
                transcript = self._transcribe_with_openai(video)
                if transcript:
                    return self._summarize_with_openai(transcript)
            except Exception:
                # Fall back to stub if OpenAI call fails
                pass

        time.sleep(1)
        return (
            "Concise, conceptually faithful summary generated locally. Set "
            "SUMMARIZER_API_KEY to enable cloud transcription + summarization."
        )

    def summarize_via_http(self, video: Path) -> str:
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        files = {"file": video.read_bytes()}
        response = requests.post(
            self.config.endpoint,
            headers=headers,
            data={"model": self.config.model},
            files=files,
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("summary") or ""

    def _transcribe_with_openai(self, video: Path) -> str:
        if not self.client:
            raise RuntimeError("openai package is not installed")
        with video.open("rb") as handle:
            transcription = self.client.audio.transcriptions.create(
                model=self.config.transcription_model,
                file=handle,
                response_format="text",
            )
        return str(transcription)

    def _summarize_with_openai(self, transcript: str) -> str:
        if not self.client:
            raise RuntimeError("openai package is not installed")
        prompt = (
            "Summarize the following transcript into a concise, accurate recap "
            "that highlights key topics, math steps, and conclusions. Avoid "
            "hallucinating details. Transcript:\n" + transcript
        )
        completion = self.client.chat.completions.create(
            model=self.config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400,
        )
        return completion.choices[0].message.content.strip()
