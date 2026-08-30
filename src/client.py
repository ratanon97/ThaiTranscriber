"""Typhoon ASR API client wrapper.

Thin layer over the OpenAI SDK (the Typhoon API is OpenAI-compatible) that
turns responses into plain dicts and classifies errors for retry decisions.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import openai
from openai import OpenAI

from .config import TranscriberConfig

logger = logging.getLogger(__name__)

# HTTP statuses worth retrying: timeouts, conflicts, rate limits, server errors
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


def build_openai_client(config: TranscriberConfig, read_timeout: float = 120.0) -> OpenAI:
    """Create an OpenAI SDK client for the Typhoon API.

    SDK-level retries are disabled: the pipeline owns retry policy so a bad
    chunk fails fast instead of burning SDK retries times pipeline retries.
    """
    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        max_retries=0,
        timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=30.0, pool=10.0),
    )


def is_retryable(error: Exception) -> bool:
    """Whether an error is transient and worth retrying."""
    if isinstance(error, openai.APIConnectionError):  # includes APITimeoutError
        return True
    if isinstance(error, openai.APIStatusError):
        return error.status_code in RETRYABLE_STATUS_CODES
    return False


def describe_error(error: Exception) -> str:
    """Short, actionable description of an API error for logs and output."""
    if isinstance(error, openai.AuthenticationError):
        return "authentication failed - check TYPHOON_API_KEY"
    if isinstance(error, openai.RateLimitError):
        return "rate limit exceeded (100 requests/minute)"
    if isinstance(error, openai.APITimeoutError):
        return "request timed out"
    if isinstance(error, openai.APIConnectionError):
        return "connection error"
    if isinstance(error, openai.APIStatusError):
        return f"HTTP {error.status_code}: {error.message}"
    return f"{type(error).__name__}: {error}"


def response_to_dict(response: Any) -> Dict[str, Any]:
    """Normalize an SDK transcription response into a plain dict.

    Keeps `text` plus any standard fields the server actually filled in.
    Typhoon's non-standard `timestamps` blob is dropped: its times are
    relative to internal streaming windows and cannot be mapped to positions
    in the audio.
    """
    if isinstance(response, str):
        return {"text": response}
    data = response.model_dump() if hasattr(response, "model_dump") else {}
    result: Dict[str, Any] = {"text": data.get("text") or getattr(response, "text", "") or ""}
    for key in ("language", "duration", "segments", "words"):
        if data.get(key) is not None:
            result[key] = data[key]
    return result


class TyphoonASRClient:
    """Client for the Typhoon ASR transcription endpoint."""

    def __init__(self, config: TranscriberConfig):
        self.config = config
        self.client = build_openai_client(config)
        logger.info(f"Initialized Typhoon ASR client: model={config.model} base_url={config.base_url}")

    def transcribe(
        self,
        audio_file_path: Path,
        language: Optional[str] = None,
        temperature: Optional[float] = None,
        response_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe one audio file.

        Args:
            audio_file_path: Path to the audio file
            language: Language code (default: from config)
            temperature: Sampling temperature (default: from config)
            response_format: json, text, srt, verbose_json, vtt (default: from config)

        Returns:
            Dict with at least a `text` key

        Raises:
            FileNotFoundError: If the audio file does not exist
            openai.APIError: For API failures (see is_retryable / describe_error)
        """
        if not audio_file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

        params: Dict[str, Any] = {
            "model": self.config.model,
            "language": language or self.config.language,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "response_format": response_format or self.config.response_format,
        }
        logger.debug(f"Transcribing {audio_file_path.name} ({audio_file_path.stat().st_size / 1024:.0f} KB) {params}")

        with open(audio_file_path, "rb") as audio_file:
            response = self.client.audio.transcriptions.create(file=audio_file, **params)
        return response_to_dict(response)
