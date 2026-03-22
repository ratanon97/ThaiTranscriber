#!/usr/bin/env python3
"""Thai Transcriber - CLI tool for transcribing Thai audio using Typhoon ASR API.

Usage:
    # Short files (direct API call)
    python transcribe.py --file audio.wav
    python transcribe.py --file audio.mp3 --output-format json

    # Long files / m4a (auto-pipeline with chunking)
    python transcribe.py --file meeting.m4a --output-format json --output-dir ./transcriptions/

    # Explicit chunking control
    python transcribe.py --file meeting.m4a --chunk-duration 300 --output-format json

    # Resume an interrupted pipeline run
    python transcribe.py --file meeting.m4a --output-format json --resume
"""

import sys


def _check_venv() -> None:
    """Exit with a helpful message if not running inside a virtual environment."""
    in_venv = (
        hasattr(sys, "real_prefix")  # virtualenv
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)  # venv
    )
    if not in_venv:
        print(
            "Error: This script must be run inside the project's virtual environment.\n"
            "\n"
            "Activate the venv first:\n"
            "  source venv/bin/activate\n"
            "  python transcribe.py --file audio.wav\n"
            "\n"
            "Or run directly with the venv Python:\n"
            "  ./venv/bin/python transcribe.py --file audio.wav",
            file=sys.stderr,
        )
        sys.exit(1)


_check_venv()

import logging
import argparse
from pathlib import Path
from typing import Optional

from src.config import TranscriberConfig, load_env_file
from src.client import TyphoonASRClient
from src.utils import (
    validate_audio_file,
    save_text_output,
    save_json_output,
    format_transcription_summary,
    generate_output_path,
    format_file_size,
)


def setup_logging(log_level: str = "INFO") -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Transcribe Thai audio files using Typhoon ASR API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic transcription (short files)
  python transcribe.py --file audio.wav
  python transcribe.py --file audio.mp3 --output-format json

  # Long audio with automatic pipeline (m4a, wav, any format)
  python transcribe.py --file "meeting.m4a" --output-format json --output-dir ./transcriptions/

  # Custom chunk size (default: 300s = 5 min)
  python transcribe.py --file "meeting.m4a" --chunk-duration 180 --output-format json

  # Resume an interrupted run
  python transcribe.py --file "meeting.m4a" --output-format json --resume

Pipeline Mode:
  For files over 10 minutes or in m4a format, the tool automatically uses
  a chunked pipeline: split -> transcribe each chunk -> merge results.
  Use --chunk-duration to control chunk size (default: 300s = 5 minutes).
  Use --no-pipeline to force single-file mode.

Environment Variables:
  TYPHOON_API_KEY          Your Typhoon ASR API key (required)
  TYPHOON_BASE_URL         API base URL (optional)
  TYPHOON_MODEL            Model name (optional)
  TYPHOON_LANGUAGE         Language code (optional, default: th)
  TYPHOON_RESPONSE_FORMAT  Response format (optional, default: json)
  TYPHOON_TEMPERATURE      Temperature (optional, default: 0.0)
  TYPHOON_LOG_LEVEL        Logging level (optional, default: INFO)

Get your API key from: https://playground.opentyphoon.ai/asr
        """,
    )

    parser.add_argument(
        "--file", "-f",
        type=Path,
        required=True,
        help="Path to the audio file (.wav, .mp3, .flac, .ogg, .opus, .m4a)",
    )

    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="Output file path. If not specified, generates based on input filename",
    )

    parser.add_argument(
        "--output-format", "-of",
        choices=["txt", "json", "both"],
        default="txt",
        help="Output format: txt, json, or both (default: txt)",
    )

    parser.add_argument(
        "--output-dir", "-od",
        type=Path,
        help="Output directory. If not specified, uses same directory as input file",
    )

    # Pipeline options
    parser.add_argument(
        "--chunk-duration",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Split audio into chunks of this duration (default: 300s for pipeline, disabled for short files)",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted pipeline run (reuse already-transcribed chunks)",
    )

    parser.add_argument(
        "--no-pipeline",
        action="store_true",
        help="Force single-file transcription (skip chunked pipeline even for long files)",
    )

    # Existing options
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path to .env file (default: .env in current directory)",
    )

    parser.add_argument(
        "--language", "-l",
        help="Language code (default: th for Thai)",
    )

    parser.add_argument(
        "--temperature", "-t",
        type=float,
        help="Sampling temperature 0.0-1.0 (default: 0.0 for deterministic output)",
    )

    parser.add_argument(
        "--response-format", "-rf",
        choices=["json", "text", "srt", "verbose_json", "vtt"],
        help="API response format (default: from config)",
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: from config or INFO)",
    )

    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress output summary (only save to file)",
    )

    return parser.parse_args()


def run_pipeline(args: argparse.Namespace, config: TranscriberConfig, client: TyphoonASRClient) -> int:
    """Run the chunked transcription pipeline for long/convertible files."""
    from src.pipeline import TranscriptionPipeline

    chunk_duration = args.chunk_duration if args.chunk_duration else 300

    pipeline = TranscriptionPipeline(
        client=client,
        chunk_duration=chunk_duration,
    )

    try:
        pipeline.run(
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
        return 0
    except KeyboardInterrupt:
        print("\n\nInterrupted. Run with --resume to continue where you left off.")
        return 130
    except Exception as e:
        logging.getLogger(__name__).error(f"Pipeline failed: {e}")
        return 1


def run_direct(args: argparse.Namespace, config: TranscriberConfig, client: TyphoonASRClient) -> int:
    """Run direct single-file transcription (original behavior)."""
    logger = logging.getLogger(__name__)

    try:
        logger.info("Starting transcription...")
        result = client.transcribe(
            audio_file_path=args.file,
            language=config.language,
            temperature=config.temperature,
            response_format=config.response_format,
        )
        logger.info("Transcription completed successfully")
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return 1

    # Display results (unless quiet mode)
    if not args.quiet:
        print("\n" + format_transcription_summary(result))

    # Save output
    try:
        if args.output:
            output_path = args.output
            if args.output_format == "json" or (args.output_format == "both"):
                save_json_output(result, output_path)
            else:
                save_text_output(result["text"], output_path)
        else:
            if args.output_format == "txt":
                output_path = generate_output_path(args.file, "txt", output_dir=args.output_dir)
                save_text_output(result["text"], output_path)
                print(f"\n Saved to: {output_path}")
            elif args.output_format == "json":
                output_path = generate_output_path(args.file, "json", output_dir=args.output_dir)
                save_json_output(result, output_path)
                print(f"\n Saved to: {output_path}")
            elif args.output_format == "both":
                txt_path = generate_output_path(args.file, "txt", output_dir=args.output_dir)
                json_path = generate_output_path(args.file, "json", output_dir=args.output_dir)
                save_text_output(result["text"], txt_path)
                save_json_output(result, json_path)
                print(f"\n Saved to: {txt_path}")
                print(f" Saved to: {json_path}")
    except Exception as e:
        logger.error(f"Failed to save output: {e}")
        return 1

    logger.info("Transcription process completed successfully")
    return 0


def main() -> int:
    """Main entry point for the transcriber CLI."""
    args = parse_arguments()

    # Load environment variables from .env file
    load_env_file(args.env_file)

    # Load configuration
    try:
        config = TranscriberConfig.from_env()
        config.validate()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("\nMake sure to set TYPHOON_API_KEY in your .env file or environment.", file=sys.stderr)
        print("Get your API key from: https://playground.opentyphoon.ai/asr", file=sys.stderr)
        return 1

    # Override config with CLI arguments if provided
    if args.log_level:
        config.log_level = args.log_level
    if args.language:
        config.language = args.language
    if args.temperature is not None:
        config.temperature = args.temperature
    if args.response_format:
        config.response_format = args.response_format

    # Setup logging
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    # Validate input file
    try:
        validate_audio_file(args.file)
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Invalid audio file: {e}")
        return 1

    file_size = args.file.stat().st_size
    logger.info(f"Input file: {args.file}")
    logger.info(f"File size: {format_file_size(file_size)}")

    # Initialize client
    try:
        client = TyphoonASRClient(config)
    except Exception as e:
        logger.error(f"Failed to initialize client: {e}")
        return 1

    # Decide: pipeline or direct transcription
    if args.no_pipeline:
        use_pipeline = False
    else:
        from src.pipeline import should_use_pipeline
        use_pipeline = should_use_pipeline(args.file, args.chunk_duration)

    if use_pipeline:
        logger.info("Using chunked pipeline mode")
        return run_pipeline(args, config, client)
    else:
        logger.info("Using direct transcription mode")
        return run_direct(args, config, client)


if __name__ == "__main__":
    sys.exit(main())
