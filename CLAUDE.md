# ThaiTranscriber - Project Instructions for Claude

## What This Project Is

ThaiTranscriber is a CLI tool that transcribes Thai audio files using the Typhoon ASR API. Tap uses it as part of a multi-step workflow: record audio (usually m4a from a phone), transcribe it, then have Claude summarize the transcription in both Thai and English.

## Project Structure

```
ThaiTranscriber/
├── transcribe.py              # CLI entry point (requires venv)
├── src/
│   ├── __init__.py
│   ├── client.py              # Typhoon ASR API client (OpenAI SDK)
│   ├── config.py              # Config from .env / environment vars
│   ├── audio.py               # ffmpeg wrapper (convert, split, probe duration)
│   ├── pipeline.py            # Chunked transcription pipeline orchestrator
│   └── utils.py               # File validation, output helpers
├── transcriptions/            # JSON transcription outputs (gitignored)
├── summaries/                 # Summary and translation MDs (gitignored)
├── venv/                      # Python virtual environment (gitignored)
├── .env                       # API key config (gitignored, NEVER commit)
├── .env.example               # Template for .env
├── requirements.txt           # openai>=1.0.0
└── IMPLEMENTATION_PLAN.md     # Original design doc
```

## Typical Workflow

When Tap provides an m4a audio recording, follow these steps:

### Step 1: Transcribe the Audio

The tool now supports m4a directly via automatic pipeline mode. No manual ffmpeg conversion needed.

```bash
# Single command for any audio file (m4a, wav, mp3, etc.)
# Pipeline auto-activates for files >10 min or m4a format
./venv/bin/python transcribe.py --file "Recording Name.m4a" --output-format json --output-dir ./transcriptions/
```

For long recordings (1-2 hours), the pipeline automatically:
1. Converts m4a to speech-optimized opus (mono, 16kHz, 48kbps)
2. Splits into 5-minute chunks (configurable via `--chunk-duration`)
3. Transcribes each chunk sequentially with retry and progress tracking
4. Merges all results into a single JSON
5. Cleans up temporary files

**Pipeline options:**
```bash
# Custom chunk size (e.g., 3 min for more reliability)
./venv/bin/python transcribe.py --file "meeting.m4a" --chunk-duration 180 --output-format json --output-dir ./transcriptions/

# Resume an interrupted run (skips already-transcribed chunks)
./venv/bin/python transcribe.py --file "meeting.m4a" --output-format json --output-dir ./transcriptions/ --resume

# Force direct mode (skip pipeline, for short API-compatible files)
./venv/bin/python transcribe.py --file "short_clip.wav" --no-pipeline --output-format json
```

This produces a JSON file in `transcriptions/` with the raw Thai text from Typhoon ASR.

### Step 2: Summarize and Translate

After transcription, Claude should:

1. Read the JSON transcription from `transcriptions/`
2. Create a summary markdown file in `summaries/` with this structure:
   - **Header**: file name, duration, language, date
   - **English Summary**: key points, context, speaker attribution if multiple speakers
   - **Original Thai Transcription**: the raw Thai text for reference
3. If there are multiple related recordings, combine them into a single summary document
4. For longer or conversation-heavy recordings, also produce an English translation document with speaker tags `[T]` for Tap, `[Y]` for Somying, etc.

### Step 3: Clean Up

After transcription and summary are complete:

- Delete the source m4a file (the original recording -- large, no longer needed)
- Keep the JSON transcription in `transcriptions/` (small, useful reference)
- Keep the summary/translation MDs in `summaries/` (the final deliverable)
- No intermediate files to clean up (pipeline handles this automatically)

## Summary Output Format

Follow the established format in existing summaries. Example:

```markdown
# Audio Transcription Summary: [Recording Name]

**File:** [filename].m4a
**Duration:** ~X minutes
**Language:** Thai
**Date:** [date]

---

## English Summary

[Contextual summary with key points, speaker identification, and relevant details]

---

## Original Thai Transcription

[Raw Thai text from the JSON transcription]

---

*Transcribed using ThaiTranscriber with Typhoon ASR API*
```

## Important Rules

- **Always use the venv**: `transcribe.py` enforces virtual environment usage and will refuse to run under system Python
- **Never commit .env**: it contains the Typhoon API key
- **m4a is now supported**: the pipeline auto-converts m4a to opus before transcription. No manual ffmpeg step needed.
- **5-minute chunks are optimal**: for long files, the default 300s chunk duration balances API timeout risk (~35s processing per chunk) against number of API calls. Use 180s if experiencing frequent 524 timeouts.
- **Speaker attribution is approximate**: ASR output is a single text blob without speaker diarization. Claude infers speakers from context, names mentioned, and conversational patterns. Mark attribution as approximate.
- **Thai ASR quality**: Typhoon ASR handles Thai well but informal speech, slang, and code-switching (Thai-English) can produce imperfect transcriptions. Claude should interpret the intent rather than translating ASR artifacts literally.
- **File naming**: JSON files keep the original recording name. Summary MDs use underscored names with `_Summary.md` suffix.

## API Details

- **Provider**: OpenTyphoon AI (https://opentyphoon.ai)
- **Model**: typhoon-asr-realtime
- **Rate limit**: 100 requests/minute
- **Endpoint**: https://api.opentyphoon.ai/v1
- **SDK**: Uses the OpenAI Python SDK (API is OpenAI-compatible)
- **Client timeout**: read=120s (to avoid premature timeout before Cloudflare's 524 gateway timeout)
- **Pipeline retry**: exponential backoff (5s, 10s, 20s) on failure, max 3 retries per chunk
