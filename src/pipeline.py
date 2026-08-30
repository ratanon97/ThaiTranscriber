"""Chunked transcription pipeline for long audio files.

Orchestrates: convert + detect pauses -> split at pauses -> transcribe chunks
in parallel -> merge with absolute timestamps. Supports resume, per-chunk
retry on transient errors, and partial results when a chunk keeps failing.
"""

import json
import logging
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .audio import (
    Chunk,
    check_ffmpeg,
    convert_to_wav,
    format_duration,
    format_timestamp,
    needs_conversion,
    plan_cut_points,
    probe_duration,
    split_wav,
)
from .client import TyphoonASRClient, describe_error, is_retryable

logger = logging.getLogger(__name__)

# Files longer than this (seconds) use chunked processing automatically
AUTO_PIPELINE_THRESHOLD = 600  # 10 minutes

DEFAULT_CHUNK_DURATION = 300
DEFAULT_WORKERS = 6

MANIFEST_NAME = "chunks.json"

# Called after each chunk is transcribed; may enrich the result dict in place
PostProcessor = Callable[[Dict[str, Any], Chunk], Dict[str, Any]]


class ChunkFailed(Exception):
    """A chunk failed after all retries (or with a non-retryable error)."""


def transcribe_with_retry(
    client: TyphoonASRClient,
    chunk_path: Path,
    max_retries: int = 3,
    base_delay: float = 5.0,
    sleep: Optional[Callable[[float], None]] = None,
    **transcribe_kwargs: Any,
) -> Dict[str, Any]:
    """Transcribe one chunk, retrying transient errors with exponential backoff.

    Non-retryable errors (bad request, auth, ...) are raised immediately.

    Raises:
        Exception: The last error once retries are exhausted
    """
    attempt = 0
    while True:
        try:
            return client.transcribe(audio_file_path=chunk_path, **transcribe_kwargs)
        except Exception as e:
            if not is_retryable(e) or attempt >= max_retries:
                raise
            delay = base_delay * (2 ** attempt)  # 5s, 10s, 20s
            attempt += 1
            logger.warning(
                f"{chunk_path.name}: attempt {attempt}/{max_retries + 1} failed "
                f"({describe_error(e)}), retrying in {delay:.0f}s"
            )
            (sleep or time.sleep)(delay)


def merge_results(
    chunks: Sequence[Chunk],
    results: Dict[int, Dict[str, Any]],
    errors: Dict[int, str],
    source_name: str,
    duration: float,
    chunk_duration: int,
) -> Dict[str, Any]:
    """Combine per-chunk results into one transcription document.

    `text` is the full transcript; chunks that failed are represented by an
    inline marker so a reader knows audio is missing there. `segments` carries
    each chunk's text with its absolute start/end in the recording.
    """
    segments: List[Dict[str, Any]] = []
    parts: List[str] = []
    for chunk in chunks:
        segment: Dict[str, Any] = {
            "index": chunk.index,
            "start": chunk.start,
            "end": chunk.end,
            "start_formatted": format_timestamp(chunk.start),
            "end_formatted": format_timestamp(chunk.end),
        }
        result = results.get(chunk.index)
        if result is None:
            segment["text"] = ""
            segment["error"] = errors.get(chunk.index, "transcription failed")
            parts.append(
                f"[[missing {segment['start_formatted']}-{segment['end_formatted']}: transcription failed]]"
            )
        else:
            segment["text"] = (result.get("text") or "").strip()
            if "text_raw" in result:
                segment["text_raw"] = (result["text_raw"] or "").strip()
            if segment["text"]:
                parts.append(segment["text"])
        segments.append(segment)

    merged: Dict[str, Any] = {
        "text": " ".join(parts),
        "source": source_name,
        "duration_seconds": round(duration, 2),
        "duration_formatted": format_duration(duration),
        "chunks": len(chunks),
        "chunk_duration_seconds": chunk_duration,
        "pipeline": True,
        "segments": segments,
        "failed_chunks": sorted(errors),
    }
    if any("text_raw" in s for s in segments):
        merged["text_raw"] = " ".join(s.get("text_raw", s["text"]) for s in segments if s.get("text_raw", s["text"]))
    return merged


class _Progress:
    """Thread-safe progress printer for parallel chunk transcription."""

    def __init__(self, to_process: int, quiet: bool):
        self._lock = threading.Lock()
        self.to_process = to_process
        self.completed = 0
        self.start_time = time.time()
        self.quiet = quiet

    def _eta(self) -> str:
        if self.completed >= self.to_process:
            return "done"
        avg = (time.time() - self.start_time) / self.completed
        return f"ETA {format_duration(avg * (self.to_process - self.completed))}"

    def done(self, chunk: Chunk, seconds: float, chars: int) -> None:
        with self._lock:
            self.completed += 1
            if not self.quiet:
                print(f"  [{self.completed:>3}/{self.to_process}] {_span(chunk)} done ({seconds:.1f}s, {chars:,} chars) [{self._eta()}]")

    def failed(self, chunk: Chunk, message: str) -> None:
        with self._lock:
            self.completed += 1
            if not self.quiet:
                print(f"  [{self.completed:>3}/{self.to_process}] {_span(chunk)} FAILED: {message}")


def _span(chunk: Chunk) -> str:
    return f"{format_timestamp(chunk.start)}-{format_timestamp(chunk.end)}"


class TranscriptionPipeline:
    """Orchestrates chunked transcription of long audio files.

    Usage:
        pipeline = TranscriptionPipeline(client)
        result = pipeline.run(input_path=Path("meeting.m4a"), output_dir=Path("transcriptions"))
        if result["failed_chunks"]:
            ...  # partial: re-run with resume=True to retry the failed ones
    """

    def __init__(
        self,
        client: TyphoonASRClient,
        chunk_duration: int = DEFAULT_CHUNK_DURATION,
        max_retries: int = 3,
        max_workers: int = DEFAULT_WORKERS,
        smart_split: bool = True,
        post_processor: Optional[PostProcessor] = None,
        client_factory: Optional[Callable[[], TyphoonASRClient]] = None,
    ):
        """
        Args:
            client: Typhoon ASR client (its config is reused for per-thread clients)
            chunk_duration: Target seconds per chunk
            max_retries: Retries per chunk on transient errors
            max_workers: Parallel transcription workers
            smart_split: Cut chunks at detected pauses instead of fixed times
            post_processor: Optional hook applied to each chunk result (e.g. LLM correction)
            client_factory: Builds per-thread clients (default: new TyphoonASRClient from client.config)
        """
        if chunk_duration <= 0:
            raise ValueError("chunk_duration must be positive")
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.client = client
        self.chunk_duration = chunk_duration
        self.max_retries = max_retries
        self.max_workers = max_workers
        self.smart_split = smart_split
        self.post_processor = post_processor
        self.client_factory = client_factory or (lambda: TyphoonASRClient(client.config))

    def run(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        output_format: str = "json",
        output_dir: Optional[Path] = None,
        resume: bool = True,
        language: Optional[str] = None,
        temperature: Optional[float] = None,
        response_format: Optional[str] = None,
        quiet: bool = False,
    ) -> Dict[str, Any]:
        """Run the full pipeline and save the output.

        Returns:
            Merged transcription dict. `failed_chunks` lists chunk indices that
            could not be transcribed; when non-empty the working directory is
            kept so a later run with resume=True only retries those.
        """
        from .utils import save_outputs

        if not check_ffmpeg():
            raise RuntimeError("ffmpeg is required for pipeline mode. Install it first.")

        duration = probe_duration(input_path)
        if not quiet:
            print(f"\nInput: {input_path.name} ({format_duration(duration)})")

        work_dir = self._work_dir(input_path, output_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        transcribe_kwargs: Dict[str, Any] = {}
        if language:
            transcribe_kwargs["language"] = language
        if temperature is not None:
            transcribe_kwargs["temperature"] = temperature
        if response_format:
            transcribe_kwargs["response_format"] = response_format

        try:
            chunks = self._prepare_chunks(input_path, work_dir, duration, resume, quiet)
            results, errors = self._transcribe_chunks(chunks, work_dir, resume, quiet, transcribe_kwargs)
        except BaseException:
            if not quiet:
                print(f"\nPipeline interrupted. Re-run with --resume to continue.\nWorking directory: {work_dir}")
            raise

        merged = merge_results(chunks, results, errors, input_path.name, duration, self.chunk_duration)
        if not quiet:
            print(f"\nMerged: {len(merged['text']):,} characters from {len(chunks)} chunks")

        for path in save_outputs(merged, input_path, output_format, output_path=output_path, output_dir=output_dir):
            if not quiet:
                print(f"Saved: {path}")

        if errors:
            if not quiet:
                print(
                    f"\n{len(errors)} chunk(s) failed: {sorted(errors)}. Output is partial. "
                    f"Re-run with --resume to retry only those.\nWorking directory: {work_dir}"
                )
        else:
            self._cleanup(work_dir, quiet)
        return merged

    @staticmethod
    def _work_dir(input_path: Path, output_dir: Optional[Path]) -> Path:
        base = output_dir if output_dir else input_path.parent
        return base / f".work_{input_path.stem.replace(' ', '_')}"

    # -- chunking -----------------------------------------------------------

    def _prepare_chunks(self, input_path: Path, work_dir: Path, duration: float, resume: bool, quiet: bool) -> List[Chunk]:
        """Split the input into chunks, or reuse the chunks of a previous run."""
        if resume:
            existing = self._load_manifest(work_dir)
            if existing is not None:
                if not quiet:
                    print(f"Reusing {len(existing)} chunks from previous run")
                return existing

        # Any leftovers belong to a different chunking; drop them
        for stale in list(work_dir.glob("chunk_*.wav")) + list(work_dir.glob("result_*.json")):
            stale.unlink()

        if not quiet:
            mode = "pauses near" if self.smart_split else "fixed"
            print(f"Converting and splitting ({mode} {self.chunk_duration}s)...", end=" ", flush=True)

        full_wav = work_dir / "full.wav"
        silences = convert_to_wav(input_path, full_wav, detect_silence=self.smart_split)
        cut_points = plan_cut_points(duration, self.chunk_duration, silences)
        chunks = split_wav(full_wav, work_dir, cut_points)
        full_wav.unlink(missing_ok=True)
        self._save_manifest(work_dir, chunks)

        if not quiet:
            print(f"{len(chunks)} chunks")
        return chunks

    def _save_manifest(self, work_dir: Path, chunks: Sequence[Chunk]) -> None:
        data = {
            "chunk_duration": self.chunk_duration,
            "smart_split": self.smart_split,
            "chunks": [{"index": c.index, "file": c.path.name, "start": c.start, "end": c.end} for c in chunks],
        }
        (work_dir / MANIFEST_NAME).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_manifest(self, work_dir: Path) -> Optional[List[Chunk]]:
        """Return chunks from a previous run if they match this configuration."""
        manifest = work_dir / MANIFEST_NAME
        if not manifest.exists():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("chunk_duration") != self.chunk_duration:
            logger.info("Previous run used a different chunk duration; re-splitting")
            return None
        chunks = [Chunk(index=c["index"], path=work_dir / c["file"], start=c["start"], end=c["end"]) for c in data.get("chunks", [])]
        if not chunks or not all(c.path.exists() for c in chunks):
            return None
        return chunks

    # -- transcription ------------------------------------------------------

    @staticmethod
    def _cache_path(work_dir: Path, index: int) -> Path:
        return work_dir / f"result_{index:03d}.json"

    def _transcribe_chunks(
        self,
        chunks: Sequence[Chunk],
        work_dir: Path,
        resume: bool,
        quiet: bool,
        transcribe_kwargs: Dict[str, Any],
    ) -> "tuple[Dict[int, Dict[str, Any]], Dict[int, str]]":
        """Transcribe chunks in parallel. Returns (results, errors) keyed by chunk index."""
        results: Dict[int, Dict[str, Any]] = {}
        errors: Dict[int, str] = {}
        pending: List[Chunk] = []

        for chunk in chunks:
            cache = self._cache_path(work_dir, chunk.index)
            if resume and cache.exists():
                with open(cache, encoding="utf-8") as f:
                    results[chunk.index] = json.load(f)
                if not quiet:
                    print(f"  [{chunk.index + 1:>3}/{len(chunks)}] {_span(chunk)} cached")
            else:
                pending.append(chunk)

        if not pending:
            if not quiet:
                print(f"\nAll {len(chunks)} chunks already transcribed.")
            return results, errors

        if not quiet:
            print(f"\nTranscribing {len(pending)} chunks ({self.max_workers} workers):\n")

        progress = _Progress(len(pending), quiet)
        thread_local = threading.local()
        start_time = time.time()

        def worker(chunk: Chunk) -> Dict[str, Any]:
            if not hasattr(thread_local, "client"):
                thread_local.client = self.client_factory()
            t0 = time.time()
            result = transcribe_with_retry(
                thread_local.client, chunk.path, max_retries=self.max_retries, **transcribe_kwargs
            )
            if self.post_processor is not None:
                result = self.post_processor(result, chunk)
            self._write_cache(work_dir, chunk.index, result)
            progress.done(chunk, time.time() - t0, len(result.get("text") or ""))
            return result

        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        try:
            futures = {executor.submit(worker, chunk): chunk for chunk in pending}
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    results[chunk.index] = future.result()
                except Exception as e:
                    message = describe_error(e)
                    errors[chunk.index] = message
                    logger.error(f"Chunk {chunk.index:03d} ({_span(chunk)}) failed: {message}")
                    progress.failed(chunk, message)
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        executor.shutdown(wait=True)

        if not quiet:
            print(f"\nCompleted {len(pending) - len(errors)}/{len(pending)} chunks in {format_duration(time.time() - start_time)}")
        return results, errors

    @staticmethod
    def _write_cache(work_dir: Path, index: int, result: Dict[str, Any]) -> None:
        """Write a chunk result atomically so an interrupted run never leaves half a file."""
        cache = TranscriptionPipeline._cache_path(work_dir, index)
        tmp = cache.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        tmp.replace(cache)

    @staticmethod
    def _cleanup(work_dir: Path, quiet: bool) -> None:
        try:
            shutil.rmtree(work_dir)
            if not quiet:
                print("Cleaned up temporary files")
        except OSError as e:
            logger.warning(f"Could not clean up {work_dir}: {e}")


def should_use_pipeline(file_path: Path, chunk_duration: Optional[int]) -> bool:
    """Use the pipeline when chunking was requested, the format needs conversion,
    or the file is longer than AUTO_PIPELINE_THRESHOLD."""
    if chunk_duration is not None or needs_conversion(file_path):
        return True
    try:
        return probe_duration(file_path) > AUTO_PIPELINE_THRESHOLD
    except RuntimeError:
        return False
