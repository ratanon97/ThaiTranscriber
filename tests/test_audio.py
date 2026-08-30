from pathlib import Path

import pytest

from src.audio import (
    convert_to_wav,
    format_duration,
    format_timestamp,
    parse_silences,
    plan_cut_points,
    probe_duration,
    split_wav,
)
from tests.conftest import requires_ffmpeg


def test_parse_silences_pairs_start_and_end():
    log = (
        "[silencedetect @ 0x1] silence_start: 7.8\n"
        "[silencedetect @ 0x1] silence_end: 8.4 | silence_duration: 0.6\n"
        "frame=1 ...\n"
        "[silencedetect @ 0x1] silence_start: 10.65\n"
        "[silencedetect @ 0x1] silence_end: 11.29 | silence_duration: 0.64\n"
        "[silencedetect @ 0x1] silence_start: 20.0\n"  # file ends in silence: dropped
    )
    assert parse_silences(log) == [(7.8, 8.4), (10.65, 11.29)]


def test_plan_cut_points_fixed_when_no_silences():
    assert plan_cut_points(duration=1000, chunk_duration=300) == [300, 600, 900]


def test_plan_cut_points_snaps_to_nearest_pause():
    silences = [(280, 281), (296, 298), (305, 306), (610, 612)]
    cuts = plan_cut_points(duration=1000, chunk_duration=300, silences=silences, search_window=30)
    # 300 -> 297 (closest), next target 597 -> 611, next target 911 -> no pause, fixed
    assert cuts == [297.0, 611.0, 911.0]


def test_plan_cut_points_ignores_pauses_outside_window():
    cuts = plan_cut_points(duration=700, chunk_duration=300, silences=[(200, 201), (400, 401)], search_window=10)
    assert cuts == [300, 600]


def test_plan_cut_points_avoids_tiny_tail_chunk():
    # 610 s file: a cut at 600 would leave a 10 s tail; keep it in the last chunk
    assert plan_cut_points(duration=610, chunk_duration=300, search_window=30) == [300]


def test_plan_cut_points_short_file_has_no_cuts():
    assert plan_cut_points(duration=120, chunk_duration=300) == []


def test_plan_cut_points_rejects_bad_chunk_duration():
    with pytest.raises(ValueError):
        plan_cut_points(duration=100, chunk_duration=0)


def test_format_duration():
    assert format_duration(5) == "5s"
    assert format_duration(65) == "1m 05s"
    assert format_duration(3725) == "1h 02m 05s"


def test_format_timestamp():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(305.7) == "05:05"
    assert format_timestamp(3725) == "1:02:05"


@requires_ffmpeg
def test_convert_detects_silences_and_split_uses_them(tone_with_pauses: Path, tmp_path: Path):
    wav = tmp_path / "full.wav"
    silences = convert_to_wav(tone_with_pauses, wav, detect_silence=True)
    assert len(silences) == 2
    assert silences[0] == pytest.approx((4.0, 5.0), abs=0.1)
    assert silences[1] == pytest.approx((9.0, 10.0), abs=0.1)

    cuts = plan_cut_points(probe_duration(wav), chunk_duration=5, silences=silences, search_window=1)
    assert cuts == pytest.approx([4.5, 9.5], abs=0.1)

    chunks = split_wav(wav, tmp_path / "chunks", cuts)
    assert [c.index for c in chunks] == [0, 1, 2]
    assert chunks[0].start == 0
    assert chunks[0].end == pytest.approx(4.5, abs=0.1)
    assert chunks[1].start == chunks[0].end
    assert chunks[2].end == pytest.approx(12.0, abs=0.1)
    assert all(c.path.exists() for c in chunks)


@requires_ffmpeg
def test_split_without_cuts_yields_single_chunk(tone_with_pauses: Path, tmp_path: Path):
    chunks = split_wav(tone_with_pauses, tmp_path / "chunks", [])
    assert len(chunks) == 1
    assert chunks[0].end == pytest.approx(12.0, abs=0.05)
