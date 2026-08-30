#!/usr/bin/env python3
"""Thai Transcriber - CLI tool for transcribing Thai audio using Typhoon ASR API.

Usage:
    # Any audio file; long files and m4a use the chunked pipeline automatically
    python transcribe.py --file meeting.m4a --output-format json --output-dir ./transcriptions/

    # Resume an interrupted or partially failed run
    python transcribe.py --file meeting.m4a --output-format json --output-dir ./transcriptions/ --resume

Exit codes: 0 success, 1 error, 2 partial (some chunks failed; use --resume), 130 interrupted.
"""

import sys


def _check_venv() -> None:
    """Exit with a helpful message if not running inside a virtual environment."""
    in_venv = hasattr(sys, "real_prefix") or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    if not in_venv:
        print(
            "Error: This script must be run inside the project's virtual environment.\n"
            "\n"
            "Run it with the venv Python:\n"
            "  ./venv/bin/python transcribe.py --file audio.m4a",
            file=sys.stderr,
        )
        sys.exit(1)


_check_venv()

import argparse
import logging
import os
from pathlib import Path

from src.client import TyphoonASRClient, describe_error
from src.config import TranscriberConfig, load_env_file
from src.pipeline import DEFAULT_CHUNK_DURATION, DEFAULT_WORKERS, TranscriptionPipeline, should_use_pipeline
from src.utils import format_file_size, format_transcription_summary, save_outputs, validate_audio_file

EXIT_OK, EXIT_ERROR, EXIT_PARTIAL, EXIT_INTERRUPTED = 0, 1, 2, 130


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe Thai audio files using Typhoon ASR API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Pipeline mode:
  Files over 10 minutes or in a format the API cannot take directly (m4a, ...)
  are converted to 16 kHz wav, cut into ~{DEFAULT_CHUNK_DURATION}s chunks at natural pauses,
  transcribed with {DEFAULT_WORKERS} parallel workers, and merged with per-chunk timestamps.
  A chunk that keeps failing does not stop the others: the output is written
  with that span marked missing, the exit code is 2, and --resume retries it.

Environment variables (or .env):
  TYPHOON_API_KEY          Your Typhoon API key (required)
  TYPHOON_BASE_URL         API base URL (optional)
  TYPHOON_MODEL            ASR model (default: typhoon-asr-realtime)
  TYPHOON_LANGUAGE         Language code (default: th)
  TYPHOON_RESPONSE_FORMAT  Response format (default: json)
  TYPHOON_TEMPERATURE      Temperature (default: 0.0)
  TYPHOON_LOG_LEVEL        Logging level (default: INFO)

Get your API key from: https://playground.opentyphoon.ai/asr
        """,
    )
    parser.add_argument("--file", "-f", type=Path, required=True, help="Audio file (.wav, .mp3, .flac, .ogg, .opus, .m4a, ...)")
    parser.add_argument("--output", "-o", type=Path, help="Output file path (default: <output-dir>/<input stem>.<ext>)")
    parser.add_argument("--output-format", "-of", choices=["txt", "json", "both"], default="txt", help="Output format (default: txt)")
    parser.add_argument("--output-dir", "-od", type=Path, help="Output directory (default: same as input file)")

    pipeline = parser.add_argument_group("pipeline options")
    pipeline.add_argument("--chunk-duration", type=int, default=None, metavar="SECONDS", help=f"Target chunk length; forces pipeline mode (default: {DEFAULT_CHUNK_DURATION})")
    pipeline.add_argument("--workers", "-w", type=int, default=DEFAULT_WORKERS, metavar="N", help=f"Parallel transcription workers (default: {DEFAULT_WORKERS})")
    pipeline.add_argument("--resume", action="store_true", help="Reuse chunks and results from a previous interrupted/partial run")
    pipeline.add_argument("--fixed-chunks", action="store_true", help="Cut at exact intervals instead of at nearby pauses")
    pipeline.add_argument("--no-pipeline", action="store_true", help="Send the file to the API as-is, even if long")

    api = parser.add_argument_group("API options")
    api.add_argument("--env-file", type=Path, help="Path to .env file (default: ./.env)")
    api.add_argument("--language", "-l", help="Language code (default: th)")
    api.add_argument("--temperature", "-t", type=float, help="Sampling temperature 0.0-1.0 (default: 0.0)")
    api.add_argument("--response-format", "-rf", choices=["json", "text", "srt", "verbose_json", "vtt"], help="API response format")

    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level (default: WARNING, or TYPHOON_LOG_LEVEL)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    return parser.parse_args()


def run_pipeline(args: argparse.Namespace, config: TranscriberConfig, client: TyphoonASRClient) -> int:
    pipeline = TranscriptionPipeline(
        client=client,
        chunk_duration=args.chunk_duration or DEFAULT_CHUNK_DURATION,
        max_workers=args.workers,
        smart_split=not args.fixed_chunks,
    )
    try:
        result = pipeline.run(
            input_path=args.file,
            output_path=args.output,
            output_format=args.output_format,
            output_dir=args.output_dir,
            resume=args.resume,
            language=config.language,
            temperature=config.temperature,
            response_format=config.response_format,
            quiet=args.quiet,
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted. Run again with --resume to continue where you left off.", file=sys.stderr)
        sys.stderr.flush()
        os._exit(EXIT_INTERRUPTED)  # don't wait for in-flight uploads to finish
    except Exception as e:
        logging.getLogger(__name__).error(f"Pipeline failed: {describe_error(e)}")
        return EXIT_ERROR
    return EXIT_PARTIAL if result.get("failed_chunks") else EXIT_OK


def run_direct(args: argparse.Namespace, config: TranscriberConfig, client: TyphoonASRClient) -> int:
    logger = logging.getLogger(__name__)
    try:
        result = client.transcribe(
            audio_file_path=args.file,
            language=config.language,
            temperature=config.temperature,
            response_format=config.response_format,
        )
    except Exception as e:
        logger.error(f"Transcription failed: {describe_error(e)}")
        return EXIT_ERROR

    if not args.quiet:
        print("\n" + format_transcription_summary(result))
    try:
        for path in save_outputs(result, args.file, args.output_format, output_path=args.output, output_dir=args.output_dir):
            print(f"Saved: {path}")
    except OSError as e:
        logger.error(f"Failed to save output: {e}")
        return EXIT_ERROR
    return EXIT_OK


def main() -> int:
    args = parse_arguments()
    if args.workers < 1:
        print("Error: --workers must be at least 1", file=sys.stderr)
        return EXIT_ERROR
    if args.chunk_duration is not None and args.chunk_duration <= 0:
        print("Error: --chunk-duration must be positive", file=sys.stderr)
        return EXIT_ERROR

    load_env_file(args.env_file)
    try:
        config = TranscriberConfig.from_env()
        if args.language:
            config.language = args.language
        if args.temperature is not None:
            config.temperature = args.temperature
        if args.response_format:
            config.response_format = args.response_format
        config.validate()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return EXIT_ERROR

    log_level = args.log_level or os.getenv("TYPHOON_LOG_LEVEL", "WARNING")
    logging.basicConfig(level=log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logger = logging.getLogger(__name__)

    try:
        validate_audio_file(args.file)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR
    logger.info(f"Input: {args.file} ({format_file_size(args.file.stat().st_size)})")

    client = TyphoonASRClient(config)
    if not args.no_pipeline and should_use_pipeline(args.file, args.chunk_duration):
        return run_pipeline(args, config, client)
    return run_direct(args, config, client)


if __name__ == "__main__":
    sys.exit(main())
