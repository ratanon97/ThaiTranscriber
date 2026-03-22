"""Utility functions for Thai Transcriber.

Provides helpers for file validation, output formatting, and saving.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional


logger = logging.getLogger(__name__)


# Formats the Typhoon ASR API accepts directly
NATIVE_API_FORMATS = {".wav", ".mp3", ".flac", ".ogg", ".opus"}

# Additional formats supported via ffmpeg conversion in pipeline mode
CONVERTIBLE_FORMATS = {".m4a", ".aac", ".wma", ".webm", ".mp4", ".mkv"}

# All supported input formats
SUPPORTED_AUDIO_FORMATS = NATIVE_API_FORMATS | CONVERTIBLE_FORMATS


def validate_audio_file(file_path: Path) -> bool:
    """Validate that an audio file exists and has a supported format.

    Args:
        file_path: Path to the audio file

    Returns:
        True if file is valid

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is not supported
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    file_suffix = file_path.suffix.lower()
    if file_suffix not in SUPPORTED_AUDIO_FORMATS:
        raise ValueError(
            f"Unsupported audio format: {file_suffix}. "
            f"Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}"
        )

    logger.info(f"Audio file validated: {file_path.name} ({file_suffix})")
    return True


def save_text_output(text: str, output_path: Path) -> None:
    """Save transcription text to a .txt file.

    Args:
        text: Transcribed text to save
        output_path: Path where to save the text file
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)

    logger.info(f"Text output saved to: {output_path}")


def save_json_output(data: Dict[str, Any], output_path: Path, pretty: bool = True) -> None:
    """Save transcription data to a .json file.

    Args:
        data: Transcription data dictionary
        output_path: Path where to save the JSON file
        pretty: If True, format JSON with indentation for readability
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            json.dump(data, f, ensure_ascii=False)

    logger.info(f"JSON output saved to: {output_path}")


def format_transcription_summary(result: Dict[str, Any]) -> str:
    """Format transcription results into a readable summary.

    Args:
        result: Transcription result dictionary

    Returns:
        Formatted summary string
    """
    summary_lines = []
    summary_lines.append("=" * 60)
    summary_lines.append("TRANSCRIPTION RESULT")
    summary_lines.append("=" * 60)

    # Add text
    text = result.get("text", "")
    summary_lines.append(f"\nText ({len(text)} characters):")
    summary_lines.append("-" * 60)
    summary_lines.append(text)
    summary_lines.append("-" * 60)

    # Add metadata if available
    if "language" in result:
        summary_lines.append(f"\nLanguage: {result['language']}")

    if "duration" in result:
        summary_lines.append(f"Duration: {result['duration']:.2f} seconds")

    # Add segments info if available
    if "segments" in result:
        segments = result["segments"]
        summary_lines.append(f"Segments: {len(segments)}")

    # Add words info if available
    if "words" in result:
        words = result["words"]
        summary_lines.append(f"Words: {len(words)}")

    summary_lines.append("=" * 60)

    return "\n".join(summary_lines)


def generate_output_path(
    input_path: Path,
    output_format: str,
    output_dir: Optional[Path] = None,
    suffix: Optional[str] = None,
) -> Path:
    """Generate output file path based on input file and desired format.

    Args:
        input_path: Path to the input audio file
        output_format: Desired output format ('txt' or 'json')
        output_dir: Optional output directory. If None, uses same dir as input
        suffix: Optional suffix to add to filename before extension

    Returns:
        Path object for the output file

    Examples:
        >>> generate_output_path(Path("audio.wav"), "txt")
        Path("audio.txt")
        >>> generate_output_path(Path("audio.wav"), "json", suffix="_transcript")
        Path("audio_transcript.json")
    """
    stem = input_path.stem
    if suffix:
        stem = f"{stem}{suffix}"

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"{stem}.{output_format}"
    else:
        return input_path.parent / f"{stem}.{output_format}"


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format.

    Args:
        size_bytes: File size in bytes

    Returns:
        Formatted string (e.g., "1.5 MB", "256 KB")
    """
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"
