"""Audio processing utilities using ffmpeg.

Provides helpers for probing duration, converting formats, and splitting
audio files into chunks for the transcription pipeline.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Formats the Typhoon ASR API accepts directly
API_SUPPORTED_FORMATS = {".wav", ".mp3", ".flac", ".ogg", ".opus"}

# Additional formats we can convert from via ffmpeg
CONVERTIBLE_FORMATS = {".m4a", ".aac", ".wma", ".webm", ".mp4", ".mkv"}

# All formats we accept as input
ALL_SUPPORTED_FORMATS = API_SUPPORTED_FORMATS | CONVERTIBLE_FORMATS


def check_ffmpeg() -> bool:
    """Check if ffmpeg and ffprobe are available on the system."""
    for cmd in ("ffmpeg", "ffprobe"):
        if shutil.which(cmd) is None:
            logger.error(f"{cmd} not found. Install ffmpeg: https://ffmpeg.org/download.html")
            return False
    return True


def probe_duration(file_path: Path) -> float:
    """Get audio duration in seconds using ffprobe.

    Args:
        file_path: Path to the audio file

    Returns:
        Duration in seconds

    Raises:
        RuntimeError: If ffprobe fails
    """
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(file_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Could not parse duration from ffprobe output: {result.stdout.strip()}")


def needs_conversion(file_path: Path) -> bool:
    """Check if a file needs conversion before API upload."""
    return file_path.suffix.lower() not in API_SUPPORTED_FORMATS


def split_audio(
    input_path: Path,
    output_dir: Path,
    chunk_duration: int = 300,
    sample_rate: int = 16000,
) -> List[Path]:
    """Split audio into chunks, converting to speech-optimized wav format.

    Single ffmpeg pass: converts, downsamples, encodes, and splits.
    Wav is used instead of opus because the Typhoon API intermittently
    rejects specific opus payloads with 500 errors; wav has been reliable.

    Args:
        input_path: Path to the input audio file (any supported format)
        output_dir: Directory to write chunk files into
        chunk_duration: Duration of each chunk in seconds (default: 300 = 5 min)
        sample_rate: Sample rate in Hz (default: 16000, optimal for ASR)

    Returns:
        Sorted list of chunk file paths

    Raises:
        RuntimeError: If ffmpeg fails
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "chunk_%03d.wav")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-ac", "1",                    # mono
        "-ar", str(sample_rate),       # 16kHz
        "-c:a", "pcm_s16le",           # 16-bit PCM wav
        "-f", "segment",               # segment demuxer
        "-segment_time", str(chunk_duration),
        "-reset_timestamps", "1",      # clean timestamps per chunk
        pattern,
    ]

    logger.info(f"Splitting audio: {input_path.name} -> {chunk_duration}s chunks")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg split failed: {result.stderr[-500:]}")

    chunks = sorted(output_dir.glob("chunk_*.wav"))
    if not chunks:
        raise RuntimeError("ffmpeg produced no output chunks")

    logger.info(f"Split into {len(chunks)} chunks")
    return chunks


def convert_to_wav(
    input_path: Path,
    output_path: Path,
    sample_rate: int = 16000,
) -> Path:
    """Convert a single audio file to speech-optimized wav.

    Used for short files that don't need splitting but need format conversion.

    Args:
        input_path: Source audio file
        output_path: Destination wav file
        sample_rate: Sample rate in Hz

    Returns:
        Path to the converted file

    Raises:
        RuntimeError: If ffmpeg fails
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(output_path),
    ]

    logger.info(f"Converting: {input_path.name} -> {output_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr[-500:]}")

    return output_path


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "1h 23m 45s" or "5m 30s"
    """
    if seconds < 0:
        return "0s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    elif m > 0:
        return f"{m}m {s:02d}s"
    else:
        return f"{s}s"
