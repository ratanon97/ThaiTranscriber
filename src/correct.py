"""LLM post-correction of raw ASR text.

The Typhoon ASR model has no vocabulary hints, so names and English loanwords
come out phonetically. A Thai LLM pass with a glossary fixes clear ASR errors
while leaving wording intact. Correction never fails a chunk: on any error or
implausible output the raw text is kept.
"""

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from openai import OpenAI

from .audio import Chunk
from .client import build_openai_client, describe_error, is_retryable
from .config import TranscriberConfig

logger = logging.getLogger(__name__)

DEFAULT_GLOSSARY_PATH = Path("glossary.md")

# Corrected text should be roughly the same length as the input; anything
# outside this band means the model summarized, padded, or was cut off.
MIN_LENGTH_RATIO = 0.6
MAX_LENGTH_RATIO = 1.6
MAX_OUTPUT_TOKENS = 8192

SYSTEM_PROMPT = """You are a Thai transcript editor. You receive raw automatic speech recognition (ASR) output of a Thai conversation. It may contain misheard words, wrong spellings, mangled English loanwords, and missing spaces between phrases.

Correct only clear ASR errors so the text reads as what the speakers most likely said:
- Fix misspelled Thai words and wrongly transcribed English words. Write English words in English (e.g. "อินเตอเรส" -> "interest").
- Use the glossary for names, places, and terms; prefer the glossary spellings.
- Add spaces between phrases where a Thai reader would expect them.
- Keep the speakers' wording, particles (ครับ/ค่ะ/นะ/อ่ะ), slang, repetitions, and order. Do not summarize, shorten, translate, reorder, or add anything.
- If unsure, leave the original text as is.
Output only the corrected Thai text, with no preamble or explanation."""


def load_glossary(path: Optional[Path] = None) -> str:
    """Read the glossary file. A missing default file is fine; a missing explicit path is not."""
    if path is None:
        return DEFAULT_GLOSSARY_PATH.read_text(encoding="utf-8").strip() if DEFAULT_GLOSSARY_PATH.exists() else ""
    if not path.exists():
        raise FileNotFoundError(f"Glossary file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


class TranscriptCorrector:
    """Post-processor that rewrites ASR text via a Thai LLM.

    Callable as a pipeline post_processor: returns the result dict with
    `text` corrected, `text_raw` holding the original, and `corrected` flag.
    """

    def __init__(
        self,
        config: TranscriberConfig,
        glossary: str = "",
        model: Optional[str] = None,
        max_retries: int = 2,
        chat_client: Optional[OpenAI] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ):
        self.config = config
        self.glossary = glossary.strip()
        self.model = model or config.correction_model
        self.max_retries = max_retries
        self.client = chat_client or build_openai_client(config, read_timeout=180.0)
        self._sleep = sleep or time.sleep

    def __call__(self, result: Dict[str, Any], chunk: Optional[Chunk] = None) -> Dict[str, Any]:
        raw = result.get("text") or ""
        corrected = self.correct(raw, label=chunk.path.name if chunk else "input")
        return {**result, "text": corrected, "text_raw": raw, "corrected": corrected != raw}

    def _user_message(self, text: str) -> str:
        parts = []
        if self.glossary:
            parts.append(f"Glossary of names and terms that appear in these recordings:\n{self.glossary}")
        parts.append(f"Raw transcript:\n{text}")
        return "\n\n".join(parts)

    def correct(self, text: str, label: str = "input") -> str:
        """Return the corrected text, or `text` unchanged if correction is not trustworthy."""
        if not text.strip():
            return text
        attempt = 0
        while True:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": self._user_message(text)},
                    ],
                )
                break
            except Exception as e:
                if not is_retryable(e) or attempt >= self.max_retries:
                    logger.warning(f"{label}: correction skipped ({describe_error(e)}); keeping raw text")
                    return text
                delay = 3.0 * (2 ** attempt)
                attempt += 1
                logger.warning(f"{label}: correction attempt {attempt} failed ({describe_error(e)}), retrying in {delay:.0f}s")
                self._sleep(delay)

        choice = response.choices[0]
        candidate = (choice.message.content or "").strip()
        reason = self._reject_reason(text, candidate, choice.finish_reason)
        if reason:
            logger.warning(f"{label}: correction rejected ({reason}); keeping raw text")
            return text
        return candidate

    @staticmethod
    def _reject_reason(original: str, candidate: str, finish_reason: Optional[str]) -> Optional[str]:
        if finish_reason not in (None, "stop"):
            return f"finish_reason={finish_reason}"
        if not candidate:
            return "empty output"
        ratio = len(candidate) / max(len(original.strip()), 1)
        if not MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO:
            return f"length ratio {ratio:.2f} outside {MIN_LENGTH_RATIO}-{MAX_LENGTH_RATIO}"
        return None
