# ThaiTranscriber

CLI tool that transcribes Thai audio (phone recordings, meetings, 1-2 hour conversations) with the Typhoon ASR API, then cleans the text up with a Thai LLM so it is ready to summarize and translate.

## What it does

1. Converts any input (m4a, mp3, wav, ...) to 16 kHz mono wav with ffmpeg
2. Cuts it into ~5-minute chunks **at natural pauses**, not mid-word
3. Transcribes chunks **in parallel** (3 workers) with retry on transient API errors
4. Passes each chunk through a Thai LLM (`typhoon-v2.5-30b`) with your **glossary** of names and terms to fix misheard words; raw text is kept alongside
5. Merges everything into one JSON with **per-chunk timestamps**, plus an optional timestamped `.txt`

Speed is set by the Typhoon ASR service, which processes requests one at a time per key at roughly 12x real time (a 5-minute chunk takes ~25 s of server time, and extra parallel requests only queue). Expect about 5 minutes for a 1-hour recording and 10 minutes for 2 hours; the correction pass overlaps with transcription and adds only ~20 s at the end.

## Requirements

- Python 3.11+
- ffmpeg and ffprobe on PATH (`brew install ffmpeg`)
- Typhoon API key from https://playground.opentyphoon.ai/asr

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env            # add TYPHOON_API_KEY
cp glossary.example.md glossary.md   # names, places, terms for correction (optional, gitignored)
```

`transcribe.py` refuses to run outside the venv, so call it as `./venv/bin/python transcribe.py`.

## Usage

```bash
# Typical: any file, JSON + txt into transcriptions/
./venv/bin/python transcribe.py --file "Meeting.m4a" --output-format both --output-dir ./transcriptions/

# Interrupted or some chunks failed (exit code 2)? Retry only what is missing
./venv/bin/python transcribe.py --file "Meeting.m4a" --output-format both --output-dir ./transcriptions/ --resume

# Raw ASR text only, no LLM correction
./venv/bin/python transcribe.py --file "Meeting.m4a" --no-correct ...

# Tuning
--chunk-duration 180   # shorter chunks (default 300 s)
--workers 2            # fewer parallel uploads if you see timeouts (default 3)
--fixed-chunks         # cut at exact intervals instead of pauses
--no-pipeline          # send a short API-compatible file directly
--glossary path.md     # glossary other than ./glossary.md
```

Exit codes: `0` success, `1` error, `2` partial (see `failed_chunks` in the JSON; use `--resume`), `130` interrupted.

## Output

`transcriptions/<name>.json`:

```json
{
  "text": "corrected full transcript",
  "text_raw": "raw ASR transcript",
  "source": "Meeting.m4a",
  "duration_seconds": 3612.4,
  "duration_formatted": "1h 00m 12s",
  "chunks": 12,
  "chunk_duration_seconds": 300,
  "pipeline": true,
  "failed_chunks": [],
  "segments": [
    {"index": 0, "start": 0.0, "end": 297.4, "start_formatted": "00:00", "end_formatted": "04:57",
     "text": "...", "text_raw": "..."}
  ]
}
```

A chunk that could not be transcribed appears in `text` as `[[missing 25:00-30:00: transcription failed]]` and in `failed_chunks`.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `TYPHOON_API_KEY` | required | API key |
| `TYPHOON_BASE_URL` | `https://api.opentyphoon.ai/v1` | API endpoint |
| `TYPHOON_MODEL` | `typhoon-asr-realtime` | ASR model |
| `TYPHOON_CORRECTION_MODEL` | `typhoon-v2.5-30b-a3b-instruct` | LLM for post-correction |
| `TYPHOON_LANGUAGE` | `th` | Language code |
| `TYPHOON_TEMPERATURE` | `0.0` | ASR sampling temperature |
| `TYPHOON_RESPONSE_FORMAT` | `json` | ASR response format |
| `TYPHOON_LOG_LEVEL` | `WARNING` | Logging level |

## Development

```bash
./venv/bin/python -m pytest -q
```

Tests cover split planning, retry policy, merging, resume, partial failure, output saving, and the correction guardrails; the ffmpeg-backed tests generate their own tone files and skip if ffmpeg is missing.

## Project layout

```
transcribe.py        CLI entry point
src/audio.py         ffmpeg: probe, convert + silence detection, pause-aware split
src/client.py        Typhoon ASR client, error classification
src/correct.py       LLM post-correction with glossary
src/pipeline.py      chunk -> parallel transcribe -> merge, resume, partial failure
src/config.py        .env / environment configuration
src/utils.py         validation, output writing, Thai text normalization
tests/               pytest suite
```

## Notes on the ASR model

`typhoon-asr-realtime` is a 114M-parameter streaming model (~10% character error rate on Thai). It has no vocabulary prompt and returns no usable segment timestamps, which is why the tool derives timestamps from chunk boundaries and relies on the LLM pass plus glossary for names and loanwords.
