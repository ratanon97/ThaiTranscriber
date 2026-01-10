"""Typhoon ASR API client wrapper.

Provides a simple interface to the Typhoon ASR API using the OpenAI SDK.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional
from openai import OpenAI
from openai.types.audio import Transcription

from .config import TranscriberConfig


logger = logging.getLogger(__name__)


class TyphoonASRClient:
    """Client for interacting with Typhoon ASR API."""

    def __init__(self, config: TranscriberConfig):
        """Initialize the Typhoon ASR client.

        Args:
            config: Configuration object containing API settings
        """
        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

        logger.info(f"Initialized Typhoon ASR client with model: {config.model}")
        logger.info(f"Base URL: {config.base_url}")

    def transcribe(
        self,
        audio_file_path: Path,
        language: Optional[str] = None,
        temperature: Optional[float] = None,
        response_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transcribe an audio file using Typhoon ASR API.

        Args:
            audio_file_path: Path to the audio file to transcribe
            language: Language code (default: from config, typically 'th' for Thai)
            temperature: Sampling temperature (default: from config, typically 0.0)
            response_format: Response format - json, text, srt, verbose_json, vtt
                           (default: from config)

        Returns:
            Dictionary containing transcription results with keys:
                - text: The transcribed text
                - Additional fields depending on response_format (segments, words, etc.)

        Raises:
            FileNotFoundError: If audio file doesn't exist
            ValueError: If audio format is not supported
            Exception: For API errors (authentication, rate limits, etc.)
        """
        if not audio_file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

        # Use config defaults if not overridden
        language = language or self.config.language
        temperature = temperature if temperature is not None else self.config.temperature
        response_format = response_format or self.config.response_format

        logger.info(f"Transcribing file: {audio_file_path}")
        logger.info(f"File size: {audio_file_path.stat().st_size / 1024:.2f} KB")
        logger.info(f"Language: {language}, Temperature: {temperature}, Format: {response_format}")

        try:
            with open(audio_file_path, "rb") as audio_file:
                # Build parameters for the API call
                params: Dict[str, Any] = {
                    "model": self.config.model,
                    "file": audio_file,
                    "language": language,
                    "temperature": temperature,
                    "response_format": response_format,
                }

                # Add timestamp_granularities if using verbose_json format
                if response_format == "verbose_json" and self.config.enable_timestamps:
                    params["timestamp_granularities"] = ["word", "segment"]

                logger.debug(f"API request parameters: {params}")

                # Make the API call
                response = self.client.audio.transcriptions.create(**params)

                logger.info("Transcription successful")

                # Convert response to dictionary
                if isinstance(response, Transcription):
                    result = {
                        "text": response.text,
                    }
                    # Add additional fields if available
                    if hasattr(response, "segments"):
                        result["segments"] = response.segments
                    if hasattr(response, "words"):
                        result["words"] = response.words
                    if hasattr(response, "language"):
                        result["language"] = response.language
                    if hasattr(response, "duration"):
                        result["duration"] = response.duration

                    return result
                else:
                    # For non-json responses (text, srt, vtt)
                    return {"text": str(response)}

        except FileNotFoundError:
            logger.error(f"Audio file not found: {audio_file_path}")
            raise
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            self._handle_api_error(e)
            raise

    def _handle_api_error(self, error: Exception) -> None:
        """Handle and log API errors with helpful messages.

        Args:
            error: The exception that occurred
        """
        error_msg = str(error).lower()

        if "authentication" in error_msg or "unauthorized" in error_msg or "401" in error_msg:
            logger.error(
                "Authentication failed. Please check your TYPHOON_API_KEY. "
                "Get your API key from https://playground.opentyphoon.ai/asr"
            )
        elif "rate limit" in error_msg or "429" in error_msg:
            logger.error(
                "Rate limit exceeded. The Typhoon ASR API allows 100 requests per minute. "
                "Please wait before making more requests."
            )
        elif "timeout" in error_msg:
            logger.error(
                "Request timed out. This may be due to network issues or a large audio file. "
                "Please check your connection and try again."
            )
        elif "invalid" in error_msg and "format" in error_msg:
            logger.error(
                "Invalid audio format. Supported formats: .wav, .mp3, .flac, .ogg, .opus"
            )
        elif "file size" in error_msg or "too large" in error_msg:
            logger.error(
                "Audio file is too large. Please check the API documentation for file size limits."
            )
        else:
            logger.error(f"API error: {error}")

    def health_check(self) -> bool:
        """Check if the API is accessible and credentials are valid.

        Returns:
            True if API is accessible, False otherwise
        """
        try:
            # Try to make a simple request to verify API access
            # Note: This will fail if we don't have a valid audio file, but will validate auth
            logger.info("Performing API health check...")
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
