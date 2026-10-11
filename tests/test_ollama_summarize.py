"""Tests für sources/ollama_summarize.py (EXPERIMENTELL, siehe dortige
Moduldoc) - bewusst in einer eigenen Datei, damit sie beim Entfernen des
Features mit entfernt werden kann, ohne andere Tests anzufassen."""

from __future__ import annotations

import httpx

from ytdigest.config import SummaryCfg
from ytdigest.sources import ollama_summarize


class _FakeResponse:
    def __init__(self, json_data: dict) -> None:
        self._json = json_data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json


def test_summarize_success(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"response": "  Kurze Zusammenfassung.  "})

    monkeypatch.setattr(ollama_summarize.httpx, "post", fake_post)
    result = ollama_summarize.summarize("Ein langer Text.", cfg=SummaryCfg())

    assert result.text == "Kurze Zusammenfassung."
    assert result.error is None
    assert result.truncated is False
    assert captured["url"] == "http://127.0.0.1:11434/api/generate"
    assert captured["json"]["model"] == "qwen3.5:9b"
    assert "Ein langer Text." in captured["json"]["prompt"]
    assert captured["json"]["options"]["num_ctx"] == SummaryCfg().num_ctx


def test_summarize_truncates_long_input(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["prompt"] = json["prompt"]
        return _FakeResponse({"response": "ok"})

    monkeypatch.setattr(ollama_summarize.httpx, "post", fake_post)
    cfg = SummaryCfg(max_input_chars=100)
    long_text = "x" * 500
    result = ollama_summarize.summarize(long_text, cfg=cfg)

    assert result.truncated is True
    assert result.text == "ok"
    assert "x" * 500 not in captured["prompt"]
    assert "x" * 100 in captured["prompt"]


def test_summarize_uses_prompt_template_placeholders(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return _FakeResponse({"response": "ok"})

    monkeypatch.setattr(ollama_summarize.httpx, "post", fake_post)
    cfg = SummaryCfg(prompt_template="MAX={max_chars} TEXT={text}", max_chars=42)
    ollama_summarize.summarize("Hallo", cfg=cfg)

    assert captured["json"]["prompt"] == "MAX=42 TEXT=Hallo"


def test_summarize_http_error_returns_error_result(monkeypatch):
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(ollama_summarize.httpx, "post", fake_post)
    result = ollama_summarize.summarize("Text", cfg=SummaryCfg())

    assert result.text is None
    assert "nicht erreichbar" in result.error


def test_summarize_empty_response_is_error(monkeypatch):
    monkeypatch.setattr(
        ollama_summarize.httpx, "post",
        lambda url, json, timeout: _FakeResponse({"response": "   "}),
    )
    result = ollama_summarize.summarize("Text", cfg=SummaryCfg())

    assert result.text is None
    assert "leere Antwort" in result.error


def test_summarize_invalid_json_is_error(monkeypatch):
    class _BadResponse:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(ollama_summarize.httpx, "post",
                        lambda url, json, timeout: _BadResponse())
    result = ollama_summarize.summarize("Text", cfg=SummaryCfg())

    assert result.text is None
    assert "Ungültige Antwort" in result.error
