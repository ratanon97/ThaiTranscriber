"""File validation and output helpers."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audio import ALL_SUPPORTED_FORMATS

logger = logging.getLogger(__name__)


def normalize_thai(text: str) -> str:
    """Normalize ASR artifacts: decomposed nikhahit + sara aa -> sara am (ทํา -> ทำ)."""
    return text.replace("\u0e4d\u0e32", "\u0e33")


def validate_audio_file(file_path: Path) -> None:
    """Raise if the path is not an existing file with a supported extension.

    Raises:
        FileNotFoundError: If the file does not exist
        ValueError: If the path is not a file or the format is unsupported
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")
    suffix = file_path.suffix.lower()
    if suffix not in ALL_SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported audio format: {suffix}. "
            f"Supported formats: {', '.join(sorted(ALL_SUPPORTED_FORMATS))}"
        )


def format_transcript_text(result: Dict[str, Any]) -> str:
    """Plain-text rendering of a result: timestamped blocks when segments exist."""
    segments = result.get("segments")
    if not segments:
        return result.get("text", "")
    blocks = []
    for seg in segments:
        header = f"[{seg['start_formatted']}-{seg['end_formatted']}]"
        body = seg.get("text") or f"(missing: {seg.get('error', 'transcription failed')})"
        blocks.append(f"{header}\n{body}")
    return "\n\n".join(blocks) + "\n"


def generate_output_path(input_path: Path, extension: str, output_dir: Optional[Path] = None) -> Path:
    """Output path with the input's stem: <output_dir or input dir>/<stem>.<extension>."""
    base = output_dir if output_dir else input_path.parent
    return base / f"{input_path.stem}.{extension}"


def save_outputs(
    result: Dict[str, Any],
    input_path: Path,
    output_format: str,
    output_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> List[Path]:
    """Write the result as json, txt, or both. Returns the paths written.

    With an explicit output_path and format "both", the .json and .txt files
    share that path's stem.
    """
    if output_format not in ("json", "txt", "both"):
        raise ValueError(f"Unknown output format: {output_format}")

    targets: List[tuple] = []
    if output_format in ("json", "both"):
        path = output_path if output_path and output_format == "json" else None
        if path is None:
            path = output_path.with_suffix(".json") if output_path else generate_output_path(input_path, "json", output_dir)
        targets.append(("json", path))
    if output_format in ("txt", "both"):
        path = output_path if output_path and output_format == "txt" else None
        if path is None:
            path = output_path.with_suffix(".txt") if output_path else generate_output_path(input_path, "txt", output_dir)
        targets.append(("txt", path))

    written = []
    for kind, path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            if kind == "json":
                json.dump(result, f, ensure_ascii=False, indent=2)
            else:
                f.write(format_transcript_text(result))
        logger.info(f"Saved {kind} output: {path}")
        written.append(path)
    return written


def format_transcription_summary(result: Dict[str, Any]) -> str:
    """Console summary for direct (single-file) mode."""
    text = result.get("text", "")
    lines = ["=" * 60, "TRANSCRIPTION RESULT", "=" * 60, f"\nText ({len(text):,} characters):", "-" * 60, text, "-" * 60]
    if "language" in result:
        lines.append(f"Language: {result['language']}")
    if "duration" in result:
        lines.append(f"Duration: {result['duration']:.2f} seconds")
    lines.append("=" * 60)
    return "\n".join(lines)


def format_file_size(size_bytes: float) -> str:
    """Human-readable size like '1.50 MB'."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"
