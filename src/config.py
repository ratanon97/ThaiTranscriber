"""Configuration for Thai Transcriber, loaded from environment variables / .env."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

VALID_RESPONSE_FORMATS = ("json", "text", "srt", "verbose_json", "vtt")


@dataclass
class TranscriberConfig:
    """Settings for the Typhoon ASR API and output behaviour."""

    api_key: str
    base_url: str = "https://api.opentyphoon.ai/v1"
    model: str = "typhoon-asr-realtime"
    language: str = "th"
    response_format: str = "json"
    temperature: float = 0.0  # 0 = deterministic output
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "TranscriberConfig":
        """Build config from TYPHOON_* environment variables.

        Raises:
            ValueError: If TYPHOON_API_KEY is missing
        """
        api_key = os.getenv("TYPHOON_API_KEY")
        if not api_key:
            raise ValueError(
                "TYPHOON_API_KEY environment variable is required. "
                "Get your API key from https://playground.opentyphoon.ai/asr"
            )
        return cls(
            api_key=api_key,
            base_url=os.getenv("TYPHOON_BASE_URL", cls.base_url),
            model=os.getenv("TYPHOON_MODEL", cls.model),
            language=os.getenv("TYPHOON_LANGUAGE", cls.language),
            response_format=os.getenv("TYPHOON_RESPONSE_FORMAT", cls.response_format),
            temperature=float(os.getenv("TYPHOON_TEMPERATURE", cls.temperature)),
            log_level=os.getenv("TYPHOON_LOG_LEVEL", cls.log_level),
        )

    def validate(self) -> None:
        """Raise ValueError if any setting is out of range."""
        if not self.api_key:
            raise ValueError("API key cannot be empty")
        if not 0 <= self.temperature <= 1:
            raise ValueError("Temperature must be between 0 and 1")
        if self.response_format not in VALID_RESPONSE_FORMATS:
            raise ValueError(f"Response format must be one of: {', '.join(VALID_RESPONSE_FORMATS)}")


def load_env_file(env_path: Optional[Path] = None) -> None:
    """Load a .env file into the environment without overriding existing vars."""
    load_dotenv(env_path or Path(".env"), override=False)
