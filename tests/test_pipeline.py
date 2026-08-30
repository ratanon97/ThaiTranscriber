import json
import threading
from pathlib import Path
from typing import Dict, List

import httpx
import openai
import pytest

from src.audio import Chunk
from src.config import TranscriberConfig
from src.pipeline import TranscriptionPipeline, merge_results, transcribe_with_retry
from tests.conftest import requires_ffmpeg


def status_error(status: int) -> openai.APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", "https://x/v1/audio/transcriptions"))
    return openai.APIStatusError(f"HTTP {status}", response=response, body=None)


class FakeClient:
    """Stands in for TyphoonASRClient. `plan` maps chunk name -> list of outcomes."""

    def __init__(self, plan: Dict[str, List] = None, config=None):
        self.config = config or TranscriberConfig(api_key="k")
        self.plan = plan or {}
        self.calls: List[str] = []
        self._lock = threading.Lock()

    def transcribe(self, audio_file_path: Path, **kwargs):
        with self._lock:
            self.calls.append(audio_file_path.name)
            outcomes = self.plan.get(audio_file_path.name)
            outcome = outcomes.pop(0) if outcomes else {"text": f"text of {audio_file_path.stem}"}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_retry_on_transient_error_then_success(tmp_path):
    client = FakeClient({"a.wav": [status_error(500), openai.APITimeoutError(httpx.Request("POST", "https://x")), {"text": "ok"}]})
    sleeps = []
    result = transcribe_with_retry(client, tmp_path / "a.wav", max_retries=3, sleep=sleeps.append)
    assert result == {"text": "ok"}
    assert sleeps == [5.0, 10.0]


def test_no_retry_on_non_retryable_error(tmp_path):
    client = FakeClient({"a.wav": [status_error(401)]})
    with pytest.raises(openai.APIStatusError):
        transcribe_with_retry(client, tmp_path / "a.wav", max_retries=3, sleep=lambda s: None)
    assert client.calls == ["a.wav"]


def test_retries_exhausted_raises_last_error(tmp_path):
    client = FakeClient({"a.wav": [status_error(503)] * 4})
    with pytest.raises(openai.APIStatusError):
        transcribe_with_retry(client, tmp_path / "a.wav", max_retries=3, sleep=lambda s: None)
    assert len(client.calls) == 4


def _chunks(n: int, length: float = 300.0) -> List[Chunk]:
    return [Chunk(i, Path(f"chunk_{i:03d}.wav"), i * length, (i + 1) * length) for i in range(n)]


def test_merge_results_builds_text_and_segments():
    merged = merge_results(_chunks(2), {0: {"text": " one "}, 1: {"text": "two"}}, {}, "rec.m4a", 600, 300)
    assert merged["text"] == "one two"
    assert merged["failed_chunks"] == []
    assert merged["chunks"] == 2
    assert merged["segments"][1] == {
        "index": 1, "start": 300.0, "end": 600.0, "start_formatted": "05:00", "end_formatted": "10:00", "text": "two",
    }
    assert "text_raw" not in merged


def test_merge_results_marks_failed_chunks():
    merged = merge_results(_chunks(3), {0: {"text": "a"}, 2: {"text": "c"}}, {1: "HTTP 500"}, "rec.m4a", 900, 300)
    assert merged["text"] == "a [[missing 05:00-10:00: transcription failed]] c"
    assert merged["failed_chunks"] == [1]
    assert merged["segments"][1]["error"] == "HTTP 500"
    assert merged["segments"][1]["text"] == ""


def test_merge_results_carries_raw_text_when_corrected():
    results = {0: {"text": "fixed a", "text_raw": "raw a"}, 1: {"text": "b"}}
    merged = merge_results(_chunks(2), results, {}, "rec.m4a", 600, 300)
    assert merged["text"] == "fixed a b"
    assert merged["text_raw"] == "raw a b"
    assert merged["segments"][0]["text_raw"] == "raw a"


@requires_ffmpeg
def test_pipeline_end_to_end_with_resume_and_partial_failure(tone_with_pauses, tmp_path, monkeypatch):
    monkeypatch.setattr("src.pipeline.time.sleep", lambda s: None)
    out_dir = tmp_path / "out"
    # 12 s file, 5 s chunks cut at pauses -> chunks 0..2; chunk 1 fails permanently the first run
    plan = {"chunk_001.wav": [status_error(500)] * 4}
    client = FakeClient(plan)
    pipeline = TranscriptionPipeline(client, chunk_duration=5, max_workers=2, client_factory=lambda: client)

    result = pipeline.run(tone_with_pauses, output_format="both", output_dir=out_dir, resume=False, quiet=True)
    assert result["failed_chunks"] == [1]
    assert result["text"] == "text of chunk_000 [[missing 00:04-00:09: transcription failed]] text of chunk_002"
    work_dir = out_dir / ".work_tone"
    assert work_dir.exists(), "work dir kept for resume after a failure"
    assert (out_dir / "tone.json").exists() and (out_dir / "tone.txt").exists()
    assert "(missing: HTTP 500" in (out_dir / "tone.txt").read_text()

    # Resume: only chunk 1 is retried, then everything is cleaned up
    client.calls.clear()
    result = pipeline.run(tone_with_pauses, output_format="json", output_dir=out_dir, resume=True, quiet=True)
    assert client.calls == ["chunk_001.wav"]
    assert result["failed_chunks"] == []
    assert result["text"] == "text of chunk_000 text of chunk_001 text of chunk_002"
    assert not work_dir.exists()
    saved = json.loads((out_dir / "tone.json").read_text())
    assert [s["start_formatted"] for s in saved["segments"]] == ["00:00", "00:04", "00:09"]


@requires_ffmpeg
def test_pipeline_post_processor_runs_per_chunk(tone_with_pauses, tmp_path):
    client = FakeClient()
    seen = []

    def post(result, chunk):
        seen.append(chunk.index)
        return {**result, "text_raw": result["text"], "text": result["text"].upper()}

    pipeline = TranscriptionPipeline(client, chunk_duration=5, max_workers=3, post_processor=post, client_factory=lambda: client)
    result = pipeline.run(tone_with_pauses, output_format="json", output_dir=tmp_path / "out", resume=False, quiet=True)
    assert sorted(seen) == [0, 1, 2]
    assert result["text"] == "TEXT OF CHUNK_000 TEXT OF CHUNK_001 TEXT OF CHUNK_002"
    assert result["text_raw"] == "text of chunk_000 text of chunk_001 text of chunk_002"


@requires_ffmpeg
def test_pipeline_resplits_when_chunk_duration_changes(tone_with_pauses, tmp_path):
    client = FakeClient()
    out_dir = tmp_path / "out"
    work_dir = out_dir / ".work_tone"
    p1 = TranscriptionPipeline(client, chunk_duration=5, client_factory=lambda: client)
    p1._prepare_chunks(tone_with_pauses, work_dir, 12.0, resume=True, quiet=True)
    (work_dir / "result_000.json").write_text("{}")
    p2 = TranscriptionPipeline(client, chunk_duration=6, client_factory=lambda: client)
    chunks = p2._prepare_chunks(tone_with_pauses, work_dir, 12.0, resume=True, quiet=True)
    assert len(chunks) == 2
    assert not (work_dir / "result_000.json").exists(), "stale results from other chunking dropped"
