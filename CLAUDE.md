# ThaiTranscriber - Project Instructions for Claude

## What This Project Is

ThaiTranscriber is a CLI tool that transcribes Thai audio files using the Typhoon ASR API and cleans the text with a Thai LLM. Tap uses it as part of a multi-step workflow: record audio (usually m4a from a phone), transcribe it, then have Claude summarize the transcription in both Thai and English.

## Project Structure

```
ThaiTranscriber/
├── transcribe.py              # CLI entry point (requires venv)
├── src/
│   ├── client.py              # Typhoon ASR API client (OpenAI SDK), error classification
│   ├── config.py              # Config from .env / environment vars
│   ├── audio.py               # ffmpeg: probe, convert + silence detection, pause-aware split
│   ├── correct.py             # LLM post-correction of ASR text using the glossary
│   ├── pipeline.py            # chunk -> parallel transcribe -> merge, resume, partial failure
│   └── utils.py               # File validation, output writing, Thai normalization
├── tests/                     # pytest suite (./venv/bin/python -m pytest -q)
├── glossary.md                # Names/places/terms for correction (gitignored; see glossary.example.md)
├── transcriptions/            # JSON transcription outputs (gitignored)
├── summaries/                 # Summary and translation MDs (gitignored)
├── venv/                      # Python virtual environment (gitignored)
├── .env                       # API key config (gitignored, NEVER commit)
└── requirements.txt           # openai, python-dotenv, pytest
```

## Typical Workflow

When Tap provides an m4a audio recording, follow these steps:

### Step 1: Transcribe the Audio

```bash
./venv/bin/python transcribe.py --file "Recording Name.m4a" --output-format json --output-dir ./transcriptions/
```

The pipeline activates automatically for m4a or files over 10 minutes. It:
1. Converts to 16 kHz mono wav and detects pauses (one ffmpeg pass)
2. Cuts into ~5-minute chunks at the nearest pause (`--chunk-duration` to change, `--fixed-chunks` to cut at exact times)
3. Transcribes chunks with 3 in flight (`--workers`). The ASR service processes requests one at a time per key (~25 s per 5-min chunk), so more workers do not speed it up and 6+ cause timeouts
4. Runs each chunk's text through `typhoon-v2.5-30b` with `glossary.md` to fix misheard names and words (`--no-correct` to skip)
5. Merges into one JSON with `text` (corrected), `text_raw`, and `segments[]` with absolute timestamps
6. Cleans up temporary files

**Exit codes:** 0 success, 1 error, 2 partial, 130 interrupted.

**If the exit code is 2** (some chunks failed after retries): the JSON is still written with `[[missing MM:SS-MM:SS: transcription failed]]` markers and `failed_chunks`, and the work dir `transcriptions/.work_<name>/` is kept. Re-run the same command with `--resume`; only the failed chunks are retried. Do not hand-merge chunks.

**Before transcribing, check `glossary.md`**: if the recording involves people or places not listed, add them (Thai spelling + English name). The glossary is sent to the correction LLM with every chunk.

### Step 2: Summarize and Translate

After transcription, Claude should:

1. Read the JSON transcription from `transcriptions/`. Use `text` (corrected) as the primary source and `segments[]` for timestamps; consult `text_raw` if a corrected passage looks wrong.
2. Create a summary markdown file in `summaries/` with this structure:
   - **Header**: file name, duration, language, date
   - **English Summary**: key points, context, speaker attribution if multiple speakers, with `[MM:SS]` references where useful
   - **Original Thai Transcription**: the corrected Thai text for reference
3. If there are multiple related recordings, combine them into a single summary document
4. For longer or conversation-heavy recordings, also produce an English translation document with speaker tags `[T]` for Tap, `[Y]` for Somying, etc.

### Step 3: Clean Up

After transcription and summary are complete:

- Delete the source m4a file (the original recording -- large, no longer needed)
- Keep the JSON transcription in `transcriptions/` (small, useful reference)
- Keep the summary/translation MDs in `summaries/` (the final deliverable)
- If a `.work_*` directory is left in `transcriptions/`, the run was partial; resolve it with `--resume` before deleting the source audio

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

[Corrected Thai text from the JSON transcription]

---

*Transcribed using ThaiTranscriber with Typhoon ASR API*
```

## Important Rules

- **Always use the venv**: `transcribe.py` enforces virtual environment usage and will refuse to run under system Python
- **Never commit .env or glossary.md**: `.env` contains the API key; `glossary.md` contains personal names. `glossary.example.md` is the committed template.
- **Run the tests after changing src/**: `./venv/bin/python -m pytest -q` (ffmpeg-backed tests generate their own audio)
- **Chunks are wav**: opus chunks were intermittently rejected by the API with 500s; wav has been reliable.
- **Expected speed**: ~12x real time end to end (1 h recording ~5 min, 2 h ~10 min). Measured Aug 30, 2026: 2 concurrent uploads finished at 27 s and 47 s, 3 at 29/50/71 s, i.e. serialized server-side. Do not raise `--workers` to fix slowness; it only adds timeout risk.
- **Chunk boundaries fall on pauses**: `plan_cut_points` in `src/audio.py` looks for a silence within 10% of the target length. Timestamps in `segments[]` come from the real chunk durations.
- **Speaker attribution is approximate**: ASR output has no speaker diarization. Claude infers speakers from context, names mentioned, and conversational patterns. Mark attribution as approximate.
- **Thai ASR quality**: the ASR model is small (~10% character error rate); the LLM pass fixes clear errors and names but not everything. Interpret intent rather than translating artifacts literally.
- **File naming**: JSON files keep the original recording name. Summary MDs use underscored names with `_Summary.md` suffix.

## API Details

- **Provider**: OpenTyphoon AI (https://opentyphoon.ai)
- **ASR model**: typhoon-asr-realtime (114M-parameter streaming model; no prompt parameter; `verbose_json` returns no usable segments, so the tool derives timestamps from chunk boundaries)
- **Correction model**: typhoon-v2.5-30b-a3b-instruct (128K context, 200 req/min), temperature 0, output rejected and raw text kept if it is truncated or its length is implausible
- **ASR rate limit**: 100 requests/minute
- **Endpoint**: https://api.opentyphoon.ai/v1 (OpenAI-compatible, via the OpenAI Python SDK)
- **Timeouts**: ASR read timeout 120s; correction read timeout 180s
- **Retries**: pipeline-owned (SDK retries disabled). Transient errors only (connection, timeout, 408/409/429/5xx), backoff 5s/10s/20s, max 3 retries per chunk
