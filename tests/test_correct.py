from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from src.audio import Chunk
from src.config import TranscriberConfig
from src.correct import TranscriptCorrector, load_glossary
from src.utils import normalize_thai


class FakeChat:
    """Fake OpenAI client exposing chat.completions.create with scripted replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content, finish = reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


def make(replies, glossary="- สมหญิง (Somying)"):
    chat = FakeChat(replies)
    corrector = TranscriptCorrector(TranscriberConfig(api_key="k"), glossary=glossary, chat_client=chat, sleep=lambda s: None)
    return corrector, chat


def test_corrects_text_and_keeps_raw():
    corrector, chat = make([("สวัสดี สมหญิง ครับ", "stop")])
    chunk = Chunk(0, Path("chunk_000.wav"), 0, 300)
    out = corrector({"text": "สวัสดีสมหญิงครับ"}, chunk)
    assert out == {"text": "สวัสดี สมหญิง ครับ", "text_raw": "สวัสดีสมหญิงครับ", "corrected": True}
    req = chat.requests[0]
    assert req["model"] == "typhoon-v2.5-30b-a3b-instruct"
    assert req["temperature"] == 0 and req["max_tokens"] > 0
    assert "สมหญิง (Somying)" in req["messages"][1]["content"]
    assert req["messages"][1]["content"].endswith("Raw transcript:\nสวัสดีสมหญิงครับ")


def test_glossary_omitted_when_empty():
    corrector, chat = make([("x", "stop")], glossary="")
    corrector.correct("x")
    assert "Glossary" not in chat.requests[0]["messages"][1]["content"]


@pytest.mark.parametrize("reply", [("", "stop"), ("สั้น", "stop"), ("ก" * 200, "stop"), ("ok text here", "length")])
def test_rejects_implausible_output(reply):
    original = "ข้อความต้นฉบับยาวพอสมควร" * 3
    corrector, _ = make([reply])
    assert corrector.correct(original) == original


def test_retries_transient_error_then_succeeds():
    timeout = openai.APITimeoutError(httpx.Request("POST", "https://x"))
    corrector, chat = make([timeout, ("แก้แล้ว นะ ครับ", "stop")])
    assert corrector.correct("แก้แล้วนะครับ") == "แก้แล้ว นะ ครับ"
    assert len(chat.requests) == 2


def test_falls_back_to_raw_on_persistent_error():
    resp = httpx.Response(401, request=httpx.Request("POST", "https://x"))
    corrector, chat = make([openai.APIStatusError("nope", response=resp, body=None)])
    assert corrector.correct("ดิบ") == "ดิบ"
    assert len(chat.requests) == 1


def test_empty_text_is_not_sent():
    corrector, chat = make([])
    assert corrector({"text": "   "}) == {"text": "   ", "text_raw": "   ", "corrected": False}
    assert chat.requests == []


def test_load_glossary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_glossary() == ""
    (tmp_path / "glossary.md").write_text("- a\n", encoding="utf-8")
    assert load_glossary() == "- a"
    with pytest.raises(FileNotFoundError):
        load_glossary(tmp_path / "missing.md")


def test_normalize_thai_recomposes_sara_am():
    assert normalize_thai("ทํา") == "ทำ"
    assert normalize_thai("ทำ") == "ทำ"
