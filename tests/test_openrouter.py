"""Tests für sources/openrouter.py (externer Anbieter, siehe dortige Moduldoc)."""

from __future__ import annotations

import httpx

from ytdigest.config import OpenRouterCfg, SummaryCfg
from ytdigest.sources import openrouter

KEY = "sk-or-geheim-123"


class _Resp:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("kein JSON")
        return self._json


def _cfg(**kw) -> OpenRouterCfg:
    return OpenRouterCfg(api_key=KEY, model="anthropic/claude-sonnet-4.5", **kw)


def test_configured_needs_key_and_model(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert not OpenRouterCfg().configured
    assert not OpenRouterCfg(api_key=KEY).configured
    assert not OpenRouterCfg(model="m").configured
    assert _cfg().configured


def test_api_key_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "aus-env")
    cfg = OpenRouterCfg(model="m")
    assert cfg.resolved_api_key() == "aus-env"
    assert cfg.configured
    assert OpenRouterCfg(api_key="datei", model="m").resolved_api_key() == "datei"


def test_summarize_success_sends_prompt_and_auth(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json)
        return _Resp(json_data={"choices": [{"message": {"content": "  Kurz.  "}}]})

    monkeypatch.setattr(openrouter.httpx, "post", fake_post)
    result = openrouter.summarize("Ein Text.", cfg=SummaryCfg(), or_cfg=_cfg())

    assert result.text == "Kurz."
    assert result.error is None
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == f"Bearer {KEY}"
    assert captured["json"]["model"] == "anthropic/claude-sonnet-4.5"
    assert "Ein Text." in captured["json"]["messages"][0]["content"]


def test_ask_uses_question_prompt(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _Resp(json_data={"choices": [{"message": {"content": "42"}}]})

    monkeypatch.setattr(openrouter.httpx, "post", fake_post)
    result = openrouter.ask("Text", "Wie viel?", cfg=SummaryCfg(), or_cfg=_cfg())
    assert result.text == "42"
    assert "Frage: Wie viel?" in captured["json"]["messages"][0]["content"]


def test_http_error_reports_provider_message_without_key(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "post", lambda *a, **k: _Resp(
        status=402, json_data={"error": {"message": "Insufficient credits"}}))
    result = openrouter.summarize("T", cfg=SummaryCfg(), or_cfg=_cfg())
    assert result.text is None
    assert "402" in result.error and "Insufficient credits" in result.error
    assert KEY not in result.error


def test_error_inside_http_200_is_reported(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "post", lambda *a, **k: _Resp(
        json_data={"error": {"message": "Provider returned error"}}))
    result = openrouter.summarize("T", cfg=SummaryCfg(), or_cfg=_cfg())
    assert result.text is None
    assert "Provider returned error" in result.error


def test_network_error_returns_result_not_exception(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("kein Netz")

    monkeypatch.setattr(openrouter.httpx, "post", boom)
    result = openrouter.summarize("T", cfg=SummaryCfg(), or_cfg=_cfg())
    assert result.text is None
    assert "nicht erreichbar" in result.error
    assert KEY not in result.error


def test_empty_and_malformed_answers(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "post", lambda *a, **k: _Resp(
        json_data={"choices": [{"message": {"content": None}}]}))
    assert "leere" in openrouter.summarize("T", cfg=SummaryCfg(), or_cfg=_cfg()).error
    monkeypatch.setattr(openrouter.httpx, "post", lambda *a, **k: _Resp(json_data={"x": 1}))
    assert "Ungültige" in openrouter.summarize("T", cfg=SummaryCfg(), or_cfg=_cfg()).error


def test_unconfigured_makes_no_request(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def fail(*a, **k):
        raise AssertionError("darf nicht aufgerufen werden")

    monkeypatch.setattr(openrouter.httpx, "post", fail)
    result = openrouter.summarize("T", cfg=SummaryCfg(), or_cfg=OpenRouterCfg())
    assert "nicht konfiguriert" in result.error


def test_check_connection_ok(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "get", lambda *a, **k: _Resp(
        json_data={"data": {"label": "ytdigest", "limit_remaining": 4.5}}))
    ok, msg = openrouter.check_connection(_cfg())
    assert ok and "ytdigest" in msg and "4.50" in msg


def test_check_connection_rejected_key(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "get", lambda *a, **k: _Resp(status=401))
    ok, msg = openrouter.check_connection(_cfg())
    assert not ok and "abgelehnt" in msg


def test_check_connection_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    ok, msg = openrouter.check_connection(OpenRouterCfg())
    assert not ok and "Kein API-Schlüssel" in msg


def test_429_error_includes_upstream_detail_and_hint(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "post", lambda *a, **k: _Resp(
        status=429, json_data={"error": {
            "message": "Provider returned error",
            "metadata": {"provider_name": "Google AI Studio", "raw": "model is overloaded"}}}))
    result = openrouter.summarize("T", cfg=SummaryCfg(), or_cfg=_cfg())
    assert "Google AI Studio" in result.error and "model is overloaded" in result.error
    assert "Rate-Limit" in result.error
