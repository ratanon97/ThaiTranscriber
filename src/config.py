"""Configuration management for Thai Transcriber.

Loads configuration from environment variables with sensible defaults
for Thai language transcription using Typhoon ASR API.
"""

import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path


@dataclass
class TranscriberConfig:
    """Configuration for Typhoon ASR transcriber."""

    # API Configuration
    api_key: str
    base_url: str = "https://api.opentyphoon.ai/v1"
    model: str = "typhoon-asr-realtime"

    # Language Configuration
    language: str = "th"  # Thai language code

    # Audio Configuration
    # Recommended settings for best accuracy with Thai audio
    sample_rate: Optional[int] = None  # Let API handle auto-detection

    # Output Configuration
    response_format: str = "json"  # json, text, srt, verbose_json, vtt
    temperature: float = 0.0  # Lower temperature for more deterministic output

    # Feature flags
    enable_timestamps: bool = True  # Request word-level timestamps if available
    enable_word_confidence: bool = True  # Request confidence scores if available

    # Logging
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "TranscriberConfig":
        """Load configuration from environment variables.

        Required environment variables:
            TYPHOON_API_KEY: Your Typhoon ASR API key

        Optional environment variables:
            TYPHOON_BASE_URL: API base URL (default: https://api.opentyphoon.ai/v1)
            TYPHOON_MODEL: Model name (default: typhoon-asr-realtime)
            TYPHOON_LANGUAGE: Language code (default: th)
            TYPHOON_RESPONSE_FORMAT: Response format (default: json)
            TYPHOON_TEMPERATURE: Temperature for sampling (default: 0.0)
            TYPHOON_ENABLE_TIMESTAMPS: Enable timestamps (default: true)
            TYPHOON_LOG_LEVEL: Logging level (default: INFO)

        Returns:
            TranscriberConfig: Configuration instance

        Raises:
            ValueError: If required environment variables are missing
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
            enable_timestamps=os.getenv("TYPHOON_ENABLE_TIMESTAMPS", "true").lower() == "true",
            enable_word_confidence=os.getenv("TYPHOON_ENABLE_WORD_CONFIDENCE", "true").lower() == "true",
            log_level=os.getenv("TYPHOON_LOG_LEVEL", cls.log_level),
        )

    def validate(self) -> None:
        """Validate configuration settings.

        Raises:
            ValueError: If configuration is invalid
        """
        if not self.api_key:
            raise ValueError("API key cannot be empty")

        if self.temperature < 0 or self.temperature > 1:
            raise ValueError("Temperature must be between 0 and 1")

        valid_formats = ["json", "text", "srt", "verbose_json", "vtt"]
        if self.response_format not in valid_formats:
            raise ValueError(f"Response format must be one of: {', '.join(valid_formats)}")


def load_env_file(env_path: Optional[Path] = None) -> None:
    """Load environment variables from .env file if it exists.

    Args:
        env_path: Path to .env file. If None, looks for .env in current directory.
    """
    if env_path is None:
        env_path = Path(".env")

    if not env_path.exists():
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            # Parse KEY=VALUE format
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]

                # Only set if not already in environment
                if key not in os.environ:
                    os.environ[key] = value
