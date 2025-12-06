import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None


@dataclass
class SummarizerConfig:
    api_key: Optional[str] = os.getenv("OPENAI_API_KEY") or os.getenv("SUMMARIZER_API_KEY")
    model: str = os.getenv("SUMMARIZER_MODEL", "gpt-4o-mini")
    transcription_model: str = os.getenv("SUMMARIZER_TRANSCRIBE_MODEL", "whisper-1")
    base_url: Optional[str] = os.getenv("OPENAI_BASE_URL") or os.getenv("AZURE_OPENAI_ENDPOINT")
    endpoint: Optional[str] = os.getenv("SUMMARIZER_HTTP_ENDPOINT")
    organization: Optional[str] = os.getenv("OPENAI_ORG")
    project: Optional[str] = os.getenv("OPENAI_PROJECT")
    timeout: int = int(os.getenv("SUMMARIZER_TIMEOUT", "120"))
    api_version: Optional[str] = os.getenv("AZURE_OPENAI_API_VERSION")


class CloudSummarizerClient:
    def __init__(self, config: Optional[SummarizerConfig] = None):
        self.config = config or SummarizerConfig()
        self.client = (
            OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                organization=self.config.organization,
                project=self.config.project,
                timeout=self.config.timeout,
                api_version=self.config.api_version,
            )
            if OpenAI and self.config.api_key
            else None
        )
        self.logger = logging.getLogger(__name__)

    def summarize(self, video: Path) -> str:
        """Summarize a video using configured cloud providers.

        When cloud settings are provided, failures surface as exceptions so
        misconfigurations do not silently fall back to local stubs. If no
        cloud provider is configured, a concise local placeholder summary is
        returned so the rest of the pipeline can still execute.
        """

        cloud_configured = bool(self.config.api_key or self.config.endpoint)

        # HTTP endpoint takes precedence when explicitly configured
        if self.config.endpoint:
            if not requests:
                raise RuntimeError(
                    "requests is required for HTTP summarization; install it via requirements.txt"
                )
            try:
                return self.summarize_via_http(video)
            except Exception as exc:  # noqa: BLE001
                self.logger.error("HTTP summarization failed: %s", exc)
                if not self.client:
                    raise

        # Direct OpenAI/Azure path
        if self.config.api_key:
            if not self.client:
                raise RuntimeError("openai package is required when OPENAI_API_KEY is set")
            try:
                transcript = self._transcribe_with_openai(video)
                if transcript:
                    return self._summarize_with_openai(transcript)
            except Exception as exc:  # noqa: BLE001
                self.logger.error("OpenAI summarization failed: %s", exc)
                if cloud_configured:
                    raise

        if cloud_configured:
            raise RuntimeError("Cloud summarization was configured but all providers failed")

        time.sleep(1)
        return (
            "Concise, conceptually faithful summary generated locally. Set "
            "OPENAI_API_KEY (or SUMMARIZER_API_KEY) to enable live Whisper + GPT "
            "summaries, or provide SUMMARIZER_HTTP_ENDPOINT for a custom "
            "compatible API."
        )

    def summarize_via_http(self, video: Path) -> str:
        if not requests:
            raise RuntimeError("requests is required for HTTP summarization; install it via requirements.txt")
        if not self.config.endpoint:
            raise RuntimeError("SUMMARIZER_HTTP_ENDPOINT must be set for HTTP summarization")
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        with video.open("rb") as handle:
            files = {"file": (video.name, handle, "application/octet-stream")}
            response = requests.post(
                self.config.endpoint,
                headers=headers,
                data={"model": self.config.model},
                files=files,
                timeout=self.config.timeout,
            )
        response.raise_for_status()
        payload = response.json()
        summary = payload.get("summary")
        if not summary:
            raise RuntimeError("Cloud summarization endpoint did not return a 'summary' field")
        return summary

    def _transcribe_with_openai(self, video: Path) -> str:
        if not self.client:
            raise RuntimeError("openai package is not installed")
        with video.open("rb") as handle:
            transcription = self.client.audio.transcriptions.create(
                model=self.config.transcription_model,
                file=handle,
                response_format="text",
            )

        normalized = str(transcription).strip()
        if not normalized:
            normalized = (
                "Audio contained no discernible speech. Produce a concise summary "
                "noting the absence of spoken content."
            )
        return normalized

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
