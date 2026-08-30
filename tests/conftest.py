import shutil
import subprocess
from pathlib import Path

import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def make_test_wav(path: Path, spec: str, duration: float) -> Path:
    """Generate a wav with ffmpeg's lavfi: `spec` is an aevalsrc expression."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"aevalsrc=exprs='{spec}':s=16000:d={duration}",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def tone_with_pauses(tmp_path: Path) -> Path:
    """12 s file: tone 0-4 s, silence 4-5 s, tone 5-9 s, silence 9-10 s, tone 10-12 s."""
    spec = "0.5*sin(440*2*PI*t)*(lt(t,4)+between(t,5,9)+gt(t,10))"
    return make_test_wav(tmp_path / "tone.wav", spec, 12)
