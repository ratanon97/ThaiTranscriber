"""Chunked transcription pipeline for long audio files.

Orchestrates: split -> parallel transcribe -> merge
with progress tracking, retry logic, and resume capability.
"""

import json
import logging
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audio import (
    check_ffmpeg,
    convert_to_wav,
    format_duration,
    needs_conversion,
    probe_duration,
    split_audio,
)
from .client import TyphoonASRClient

logger = logging.getLogger(__name__)

# Auto-pipeline threshold: files longer than this (seconds) use chunked processing
AUTO_PIPELINE_THRESHOLD = 600  # 10 minutes


def transcribe_with_retry(
    client: TyphoonASRClient,
    chunk_path: Path,
    max_retries: int = 3,
    base_delay: float = 5.0,
    **transcribe_kwargs,
) -> Dict[str, Any]:
    """Transcribe a single chunk with exponential backoff on failure.

    Args:
        client: Typhoon ASR client
        chunk_path: Path to the audio chunk
        max_retries: Maximum retry attempts
        base_delay: Base delay in seconds (doubles each retry)
        **transcribe_kwargs: Extra args passed to client.transcribe()

    Returns:
        Transcription result dict

    Raises:
        Exception: If all retries are exhausted
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return client.transcribe(audio_file_path=chunk_path, **transcribe_kwargs)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)  # 5s, 10s, 20s
                logger.warning(
                    f"{chunk_path.name}: attempt {attempt + 1}/{max_retries + 1} failed "
                    f"({type(e).__name__}), retrying in {delay:.0f}s"
                )
                time.sleep(delay)
            else:
                logger.error(f"{chunk_path.name}: all {max_retries + 1} attempts failed")
    raise last_error


def _cache_result(work_dir: Path, index: int, result: dict) -> None:
    """Write result to cache with atomic rename to prevent corruption."""
    cache_path = work_dir / f"result_{index:03d}.json"
    tmp_path = cache_path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    tmp_path.rename(cache_path)


class _ProgressTracker:
    """Thread-safe progress tracker for parallel chunk transcription."""

    def __init__(self, total: int, cached: int, chunk_duration: int, quiet: bool):
        self._lock = threading.Lock()
        self.total = total
        self.cached = cached
        self.to_process = total - cached
        self.completed = 0
        self.start_time = time.time()
        self.chunk_duration = chunk_duration
        self.quiet = quiet

    def report_cached(self, index: int) -> None:
        if self.quiet:
            return
        with self._lock:
            print(f"  [{index + 1:>3}/{self.total}] {self._time_range(index)} -- cached (skipped)")

    def report_done(self, index: int, chunk_time: float, chars: int) -> None:
        with self._lock:
            self.completed += 1
            if self.quiet:
                return
            elapsed = time.time() - self.start_time
            if self.completed > 0 and self.completed < self.to_process:
                avg = elapsed / self.completed
                remaining = avg * (self.to_process - self.completed)
                eta_str = f"ETA {format_duration(remaining)}"
            elif self.completed >= self.to_process:
                eta_str = "done"
            else:
                eta_str = "estimating..."
            print(
                f"  [{self.completed:>3}/{self.to_process}] "
                f"{self._time_range(index)} "
                f"done ({chunk_time:.1f}s, {chars:,} chars) [{eta_str}]"
            )

    def _time_range(self, index: int) -> str:
        start = index * self.chunk_duration
        end = (index + 1) * self.chunk_duration
        return f"{self._fmt(start)}-{self._fmt(end)}"

    @staticmethod
    def _fmt(seconds: int) -> str:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


class TranscriptionPipeline:
    """Orchestrates chunked transcription of long audio files.

    Usage:
        pipeline = TranscriptionPipeline(client, chunk_duration=300)
        result = pipeline.run(
            input_path=Path("meeting.m4a"),
            output_path=Path("transcriptions/meeting.json"),
        )
    """

    def __init__(
        self,
        client: TyphoonASRClient,
        chunk_duration: int = 300,
        max_retries: int = 3,
        max_workers: int = 3,
    ):
        """Initialize the pipeline.

        Args:
            client: Typhoon ASR client instance
            chunk_duration: Seconds per chunk (default: 300 = 5 min)
            max_retries: Max retries per chunk on failure
            max_workers: Number of parallel transcription workers (default: 3)
        """
        self.client = client
        self.chunk_duration = chunk_duration
        self.max_retries = max_retries
        self.max_workers = max_workers

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
        """Run the full transcription pipeline.

        Args:
            input_path: Path to the input audio file
            output_path: Explicit output path (overrides auto-generation)
            output_format: Output format (json, txt, both)
            output_dir: Output directory for auto-generated paths
            resume: If True, skip already-transcribed chunks
            language: Language override
            temperature: Temperature override
            response_format: API response format override
            quiet: Suppress progress output

        Returns:
            Merged transcription result dict
        """
        if not check_ffmpeg():
            raise RuntimeError("ffmpeg is required for pipeline mode. Install it first.")

        # Probe input duration
        duration = probe_duration(input_path)
        duration_str = format_duration(duration)
        if not quiet:
            print(f"\nInput: {input_path.name} ({duration_str})")

        # Set up working directory for intermediate files
        work_dir = self._get_work_dir(input_path, output_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        transcribe_kwargs = {}
        if language:
            transcribe_kwargs["language"] = language
        if temperature is not None:
            transcribe_kwargs["temperature"] = temperature
        if response_format:
            transcribe_kwargs["response_format"] = response_format

        try:
            # Step 1: Split audio into chunks
            chunks = self._split(input_path, work_dir, quiet)

            # Step 2: Transcribe chunks (parallel or sequential)
            if self.max_workers > 1:
                results = self._transcribe_chunks_parallel(chunks, work_dir, resume, quiet, transcribe_kwargs)
            else:
                results = self._transcribe_chunks_sequential(chunks, work_dir, resume, quiet, transcribe_kwargs)

            # Step 3: Merge results
            merged = self._merge_results(results, input_path, duration)
            if not quiet:
                total_chars = len(merged.get("text", ""))
                print(f"\nMerged: {total_chars:,} characters from {len(results)} chunks")

            # Step 4: Save output
            self._save_output(merged, input_path, output_path, output_format, output_dir, quiet)

            # Step 5: Cleanup working directory
            self._cleanup(work_dir, quiet)

            return merged

        except Exception:
            logger.info(f"Working directory preserved for resume: {work_dir}")
            if not quiet:
                print(f"\nPipeline interrupted. Resume with --resume flag.")
                print(f"Working directory: {work_dir}")
            raise

    def _get_work_dir(self, input_path: Path, output_dir: Optional[Path]) -> Path:
        """Get the working directory for intermediate files."""
        base = output_dir if output_dir else input_path.parent
        safe_name = input_path.stem.replace(" ", "_")
        return base / f".work_{safe_name}"

    def _split(self, input_path: Path, work_dir: Path, quiet: bool) -> List[Path]:
        """Split audio into chunks. Reuses existing chunks if present."""
        existing_chunks = sorted(work_dir.glob("chunk_*.wav"))
        if existing_chunks:
            if not quiet:
                print(f"Reusing {len(existing_chunks)} existing chunks from previous run")
            return existing_chunks

        if not quiet:
            print(f"Splitting into {self.chunk_duration}s chunks...", end=" ", flush=True)

        chunks = split_audio(
            input_path=input_path,
            output_dir=work_dir,
            chunk_duration=self.chunk_duration,
        )

        if not quiet:
            print(f"{len(chunks)} chunks")

        return chunks

    def _transcribe_chunks_parallel(
        self,
        chunks: List[Path],
        work_dir: Path,
        resume: bool,
        quiet: bool,
        transcribe_kwargs: dict,
    ) -> List[Dict[str, Any]]:
        """Transcribe all chunks in parallel with progress tracking."""
        total = len(chunks)
        results = [None] * total
        chunks_to_process = []
        cached_count = 0

        # Phase 1: Load cached results
        for i, chunk in enumerate(chunks):
            cache_path = work_dir / f"result_{i:03d}.json"
            if resume and cache_path.exists():
                with open(cache_path, encoding="utf-8") as f:
                    results[i] = json.load(f)
                cached_count += 1

        progress = _ProgressTracker(total, cached_count, self.chunk_duration, quiet)

        # Report cached chunks
        for i in range(total):
            if results[i] is not None:
                progress.report_cached(i)
            else:
                chunks_to_process.append((i, chunks[i]))

        if not chunks_to_process:
            if not quiet:
                print(f"\nAll {total} chunks already cached.")
            return results

        if not quiet:
            workers_str = f"{self.max_workers} workers" if self.max_workers > 1 else "1 worker"
            print(f"\nTranscribing {len(chunks_to_process)} chunks ({workers_str}):\n")

        start_time = time.time()
        chunk_times = []  # Track individual chunk times for speedup estimate
        chunk_times_lock = threading.Lock()
        cancel_event = threading.Event()

        # Per-thread clients to avoid sharing httpx connection pool across threads
        _thread_local = threading.local()

        def _get_thread_client() -> TyphoonASRClient:
            if not hasattr(_thread_local, "client"):
                _thread_local.client = TyphoonASRClient(self.client.config)
            return _thread_local.client

        # Phase 2: Transcribe remaining chunks in parallel
        def _process_chunk(index: int, chunk_path: Path) -> tuple:
            if cancel_event.is_set():
                raise RuntimeError("Cancelled")
            chunk_start = time.time()
            client = _get_thread_client()
            result = transcribe_with_retry(
                client,
                chunk_path,
                max_retries=self.max_retries,
                **transcribe_kwargs,
            )
            _cache_result(work_dir, index, result)
            chunk_time = time.time() - chunk_start
            with chunk_times_lock:
                chunk_times.append(chunk_time)
            chars = len(result.get("text", ""))
            progress.report_done(index, chunk_time, chars)
            return index, result

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_index = {}
            for index, chunk_path in chunks_to_process:
                future = executor.submit(_process_chunk, index, chunk_path)
                future_to_index[future] = index

            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    idx, result = future.result()
                    results[idx] = result
                except Exception as e:
                    # Signal other workers to stop, cancel queued futures
                    cancel_event.set()
                    for f in future_to_index:
                        f.cancel()
                    raise RuntimeError(
                        f"Chunk {index:03d} failed after retries: {e}. "
                        f"Run with --resume to retry failed chunks."
                    ) from e

        total_time = time.time() - start_time
        if not quiet:
            processed = len(chunks_to_process)
            print(f"\nCompleted {processed} chunks in {format_duration(total_time)}", end="")
            if cached_count > 0:
                print(f" ({cached_count} cached)", end="")
            if self.max_workers > 1 and chunk_times:
                seq_estimate = sum(chunk_times)
                if seq_estimate > total_time * 1.2:
                    speedup = seq_estimate / total_time
                    print(f" (~{speedup:.1f}x vs sequential)", end="")
            print()

        return results

    def _transcribe_chunks_sequential(
        self,
        chunks: List[Path],
        work_dir: Path,
        resume: bool,
        quiet: bool,
        transcribe_kwargs: dict,
    ) -> List[Dict[str, Any]]:
        """Transcribe all chunks sequentially with progress tracking (fallback)."""
        total = len(chunks)
        results = []
        start_time = time.time()
        skipped = 0

        if not quiet:
            print(f"\nTranscribing {total} chunks:\n")

        for i, chunk in enumerate(chunks):
            # Check for cached result (resume support)
            cache_path = work_dir / f"result_{i:03d}.json"
            if resume and cache_path.exists():
                with open(cache_path, encoding="utf-8") as f:
                    results.append(json.load(f))
                skipped += 1
                if not quiet:
                    print(f"  [{i + 1:>3}/{total}] {chunk.name} -- cached (skipped)")
                continue

            # Inter-request delay (skip for first non-cached chunk)
            if i > 0 and (i - skipped) > 0:
                time.sleep(2.0)

            # Progress display
            elapsed = time.time() - start_time
            completed_count = i - skipped
            if completed_count > 0:
                avg_time = elapsed / completed_count
                remaining = avg_time * (total - skipped - completed_count)
                eta_str = f"ETA {format_duration(remaining)}"
            else:
                eta_str = "estimating..."

            if not quiet:
                time_range = self._chunk_time_range(i, self.chunk_duration)
                print(f"  [{i + 1:>3}/{total}] {time_range} ...", end=" ", flush=True)

            # Transcribe with retry
            chunk_start = time.time()
            result = transcribe_with_retry(
                self.client,
                chunk,
                max_retries=self.max_retries,
                **transcribe_kwargs,
            )

            # Cache result
            _cache_result(work_dir, i, result)

            results.append(result)

            chunk_time = time.time() - chunk_start
            chars = len(result.get("text", ""))
            if not quiet:
                print(f"done ({chunk_time:.1f}s, {chars:,} chars) [{eta_str}]")

        if not quiet:
            total_time = time.time() - start_time
            print(f"\nCompleted in {format_duration(total_time)}", end="")
            if skipped > 0:
                print(f" ({skipped} cached, {total - skipped} transcribed)", end="")
            print()

        return results

    def _chunk_time_range(self, index: int, chunk_duration: int) -> str:
        """Format the time range for a chunk like '05:00-10:00'."""
        start = index * chunk_duration
        end = (index + 1) * chunk_duration
        return f"{self._fmt_mm_ss(start)}-{self._fmt_mm_ss(end)}"

    @staticmethod
    def _fmt_mm_ss(seconds: int) -> str:
        """Format seconds as MM:SS or H:MM:SS."""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _merge_results(
        self,
        results: List[Dict[str, Any]],
        input_path: Path,
        duration: float,
    ) -> Dict[str, Any]:
        """Merge chunk results into a single transcription result."""
        texts = [r.get("text", "") for r in results]
        merged_text = " ".join(t.strip() for t in texts if t.strip())

        return {
            "text": merged_text,
            "source": input_path.name,
            "duration_seconds": round(duration, 2),
            "duration_formatted": format_duration(duration),
            "chunks": len(results),
            "chunk_duration_seconds": self.chunk_duration,
            "pipeline": True,
        }

    def _save_output(
        self,
        merged: Dict[str, Any],
        input_path: Path,
        output_path: Optional[Path],
        output_format: str,
        output_dir: Optional[Path],
        quiet: bool,
    ) -> None:
        """Save the merged result to file(s)."""
        from .utils import save_json_output, save_text_output, generate_output_path

        if output_path:
            if output_format in ("json", "both"):
                save_json_output(merged, output_path)
                if not quiet:
                    print(f"\nSaved: {output_path}")
            if output_format in ("txt", "both"):
                txt_path = output_path.with_suffix(".txt") if output_format == "both" else output_path
                save_text_output(merged["text"], txt_path)
                if not quiet:
                    print(f"Saved: {txt_path}")
        else:
            if output_format in ("json", "both"):
                path = generate_output_path(input_path, "json", output_dir=output_dir)
                save_json_output(merged, path)
                if not quiet:
                    print(f"\nSaved: {path}")
            if output_format in ("txt", "both"):
                path = generate_output_path(input_path, "txt", output_dir=output_dir)
                save_text_output(merged["text"], path)
                if not quiet:
                    print(f"Saved: {path}")

    def _cleanup(self, work_dir: Path, quiet: bool) -> None:
        """Remove the working directory after successful completion."""
        try:
            shutil.rmtree(work_dir)
            logger.info(f"Cleaned up working directory: {work_dir}")
            if not quiet:
                print(f"Cleaned up temporary files")
        except OSError as e:
            logger.warning(f"Could not clean up {work_dir}: {e}")


def should_use_pipeline(file_path: Path, chunk_duration: Optional[int]) -> bool:
    """Decide whether to use the pipeline for a given file.

    Uses pipeline if:
    - User explicitly requested chunking (--chunk-duration flag)
    - File duration exceeds AUTO_PIPELINE_THRESHOLD
    - File needs format conversion (e.g., m4a)

    Args:
        file_path: Input audio file
        chunk_duration: User-specified chunk duration (None if not set)

    Returns:
        True if pipeline should be used
    """
    # Explicit chunk duration always means pipeline
    if chunk_duration is not None:
        return True

    # m4a and other non-API formats need pipeline for conversion
    if needs_conversion(file_path):
        try:
            duration = probe_duration(file_path)
            # Short convertible files: just convert, no chunking needed
            # But still route through pipeline for the conversion step
            return True
        except RuntimeError:
            return True

    # Check duration for API-supported formats
    try:
        duration = probe_duration(file_path)
        if duration > AUTO_PIPELINE_THRESHOLD:
            return True
    except RuntimeError:
        pass

    return False
