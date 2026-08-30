"""Audio processing utilities using ffmpeg.

Provides helpers for probing duration, detecting silences, converting to a
speech-optimized wav, and splitting audio into chunks at natural pauses.
"""

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Formats the Typhoon ASR API accepts directly
API_SUPPORTED_FORMATS = {".wav", ".mp3", ".flac", ".ogg", ".opus"}

# Additional formats we can convert from via ffmpeg
CONVERTIBLE_FORMATS = {".m4a", ".aac", ".wma", ".webm", ".mp4", ".mkv"}

# All formats we accept as input
ALL_SUPPORTED_FORMATS = API_SUPPORTED_FORMATS | CONVERTIBLE_FORMATS

# Speech-optimized wav settings: mono, 16 kHz, 16-bit PCM
WAV_CODEC_ARGS = ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]

# silencedetect defaults: anything below -30 dB for at least 0.4 s is a pause
SILENCE_NOISE_DB = -30
SILENCE_MIN_DURATION = 0.4

_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


@dataclass(frozen=True)
class Chunk:
    """One audio chunk and its absolute position in the source recording."""

    index: int
    path: Path
    start: float  # seconds from the start of the source file
    end: float


def check_ffmpeg() -> bool:
    """Check if ffmpeg and ffprobe are available on the system."""
    for cmd in ("ffmpeg", "ffprobe"):
        if shutil.which(cmd) is None:
            logger.error(f"{cmd} not found. Install ffmpeg: https://ffmpeg.org/download.html")
            return False
    return True


def _run(cmd: List[str], what: str) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{what} failed: {result.stderr.strip()[-500:]}")
    return result


def probe_duration(file_path: Path) -> float:
    """Get audio duration in seconds using ffprobe.

    Raises:
        RuntimeError: If ffprobe fails or returns no duration
    """
    result = _run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(file_path),
        ],
        "ffprobe",
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Could not parse duration from ffprobe output: {result.stdout.strip()!r}")


def needs_conversion(file_path: Path) -> bool:
    """Check if a file needs conversion before API upload."""
    return file_path.suffix.lower() not in API_SUPPORTED_FORMATS


def parse_silences(ffmpeg_stderr: str) -> List[Tuple[float, float]]:
    """Parse silencedetect log lines into (start, end) pairs.

    A trailing silence_start with no matching silence_end (file ends in
    silence) is dropped.
    """
    silences: List[Tuple[float, float]] = []
    pending_start: Optional[float] = None
    for line in ffmpeg_stderr.splitlines():
        m = _SILENCE_START_RE.search(line)
        if m:
            pending_start = float(m.group(1))
            continue
        m = _SILENCE_END_RE.search(line)
        if m and pending_start is not None:
            silences.append((pending_start, float(m.group(1))))
            pending_start = None
    return silences


def convert_to_wav(
    input_path: Path,
    output_path: Path,
    detect_silence: bool = False,
) -> List[Tuple[float, float]]:
    """Convert any supported audio file to speech-optimized wav in one pass.

    Args:
        input_path: Source audio file
        output_path: Destination wav file
        detect_silence: If True, also run silencedetect during the same
            decode pass and return the detected silences.

    Returns:
        List of (start, end) silences in seconds (empty unless detect_silence)

    Raises:
        RuntimeError: If ffmpeg fails
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-nostats", "-v", "info", "-i", str(input_path)]
    if detect_silence:
        cmd += ["-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_DURATION}"]
    cmd += WAV_CODEC_ARGS + [str(output_path)]

    logger.info(f"Converting: {input_path.name} -> {output_path.name}")
    result = _run(cmd, "ffmpeg conversion")
    return parse_silences(result.stderr) if detect_silence else []


def plan_cut_points(
    duration: float,
    chunk_duration: float,
    silences: Sequence[Tuple[float, float]] = (),
    search_window: Optional[float] = None,
) -> List[float]:
    """Choose where to cut a recording into chunks.

    For each multiple of chunk_duration, pick the midpoint of the silence
    closest to it within +/- search_window (default: 10% of chunk_duration,
    at least 5 s). If no silence is nearby, cut at the target time.

    Args:
        duration: Total length of the recording in seconds
        chunk_duration: Target chunk length in seconds
        silences: (start, end) pairs from silence detection
        search_window: How far from the target to look for a pause

    Returns:
        Ascending list of cut times strictly inside (0, duration).
    """
    if chunk_duration <= 0:
        raise ValueError("chunk_duration must be positive")
    if search_window is None:
        search_window = max(5.0, chunk_duration * 0.1)

    midpoints = sorted((s + e) / 2 for s, e in silences)
    cuts: List[float] = []
    target = chunk_duration
    # Don't leave a tiny tail chunk: it transcribes worse than a slightly
    # longer final chunk.
    min_tail = min(search_window, chunk_duration * 0.25)
    while target < duration - min_tail:
        candidates = [m for m in midpoints if abs(m - target) <= search_window]
        cut = min(candidates, key=lambda m: abs(m - target)) if candidates else target
        if cut <= (cuts[-1] if cuts else 0.0):
            cut = target
        cuts.append(round(cut, 3))
        target = cut + chunk_duration
    return cuts


def split_wav(wav_path: Path, output_dir: Path, cut_points: Sequence[float]) -> List[Chunk]:
    """Split a wav file at the given times without re-encoding.

    Chunk boundaries in the returned list come from ffprobe of the actual
    output files, so they are exact even if ffmpeg snaps a cut slightly.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "chunk_%03d.wav"

    if cut_points:
        _run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(wav_path),
                "-c", "copy",
                "-f", "segment",
                "-segment_times", ",".join(f"{c:.3f}" for c in cut_points),
                "-reset_timestamps", "1",
                str(pattern),
            ],
            "ffmpeg split",
        )
    else:
        shutil.copyfile(wav_path, output_dir / "chunk_000.wav")

    paths = sorted(output_dir.glob("chunk_*.wav"))
    if not paths:
        raise RuntimeError("ffmpeg produced no output chunks")

    chunks: List[Chunk] = []
    position = 0.0
    for i, path in enumerate(paths):
        length = probe_duration(path)
        chunks.append(Chunk(index=i, path=path, start=round(position, 3), end=round(position + length, 3)))
        position += length
    logger.info(f"Split into {len(chunks)} chunks")
    return chunks


def format_duration(seconds: float) -> str:
    """Format seconds as a human-readable duration like '1h 23m 45s' or '5m 30s'."""
    if seconds < 0:
        return "0s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def format_timestamp(seconds: float) -> str:
    """Format seconds as a clock position like '05:00' or '1:23:45'."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
