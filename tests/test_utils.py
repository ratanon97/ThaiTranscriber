import json
from pathlib import Path

import pytest

from src.utils import format_transcript_text, save_outputs, validate_audio_file


def test_save_outputs_both_with_explicit_path_writes_json_and_txt(tmp_path):
    result = {"text": "hello"}
    written = save_outputs(result, Path("in.wav"), "both", output_path=tmp_path / "custom.json")
    assert written == [tmp_path / "custom.json", tmp_path / "custom.txt"]
    assert json.loads((tmp_path / "custom.json").read_text()) == result
    assert (tmp_path / "custom.txt").read_text() == "hello"


def test_save_outputs_default_paths_use_output_dir(tmp_path):
    written = save_outputs({"text": "x"}, Path("/a/My Rec.m4a"), "json", output_dir=tmp_path / "t")
    assert written == [tmp_path / "t" / "My Rec.json"]


def test_save_outputs_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError):
        save_outputs({"text": "x"}, Path("in.wav"), "pdf", output_dir=tmp_path)


def test_format_transcript_text_with_segments():
    result = {
        "text": "ignored",
        "segments": [
            {"start_formatted": "00:00", "end_formatted": "05:00", "text": "first"},
            {"start_formatted": "05:00", "end_formatted": "10:00", "text": "", "error": "HTTP 500"},
        ],
    }
    assert format_transcript_text(result) == "[00:00-05:00]\nfirst\n\n[05:00-10:00]\n(missing: HTTP 500)\n"


def test_validate_audio_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_audio_file(tmp_path / "nope.wav")
    bad = tmp_path / "doc.pdf"
    bad.write_bytes(b"")
    with pytest.raises(ValueError):
        validate_audio_file(bad)
    good = tmp_path / "rec.m4a"
    good.write_bytes(b"")
    validate_audio_file(good)
