"""Tests für den Remote-Zweig von sources/asr.py (cfg.remote_url) - der
verteilte Betrieb mit einem separaten ASR-Worker (siehe asr_worker.py)."""

from __future__ import annotations

import httpx
import pytest

from ytdigest.config import AsrCfg
from ytdigest.sources import asr


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict) -> None:
        self.status_code = status_code
        self._json = json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self) -> dict:
        return self._json


def test_transcribe_remote_success(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, {"text": "Hallo", "source": "asr",
                                   "language": "de", "asr_model": "large-v3"})

    monkeypatch.setattr(asr.httpx, "post", fake_post)
    cfg = AsrCfg(remote_url="http://pc.local:8765")
    result = asr.transcribe("https://youtube.com/watch?v=abc", cfg=cfg, temp_dir=None)

    assert result.text == "Hallo"
    assert result.source == "asr"
    assert captured["url"] == "http://pc.local:8765/transcribe"
    assert captured["json"] == {"url": "https://youtube.com/watch?v=abc", "kind": "youtube"}


def test_transcribe_remote_unreachable_raises_distinct_exception(monkeypatch):
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(asr.httpx, "post", fake_post)
    cfg = AsrCfg(remote_url="http://pc.local:8765")

    with pytest.raises(asr.AsrWorkerUnreachable):
        asr.transcribe("https://youtube.com/watch?v=abc", cfg=cfg, temp_dir=None)


def test_transcribe_remote_503_raises_asr_unavailable(monkeypatch):
    monkeypatch.setattr(
        asr.httpx, "post",
        lambda url, json, timeout: _FakeResponse(503, {"error": "faster-whisper fehlt"}),
    )
    cfg = AsrCfg(remote_url="http://pc.local:8765")

    with pytest.raises(asr.AsrUnavailable):
        asr.transcribe("https://youtube.com/watch?v=abc", cfg=cfg, temp_dir=None)


def test_transcribe_remote_422_returns_none(monkeypatch):
    monkeypatch.setattr(
        asr.httpx, "post",
        lambda url, json, timeout: _FakeResponse(422, {"error": "kein Transkript"}),
    )
    cfg = AsrCfg(remote_url="http://pc.local:8765")

    assert asr.transcribe("https://youtube.com/watch?v=abc", cfg=cfg, temp_dir=None) is None


def test_transcribe_without_remote_url_uses_local_path(monkeypatch):
    called = {}
    monkeypatch.setattr(asr, "_require_faster_whisper", lambda: called.setdefault("local", True))
    monkeypatch.setattr(asr, "_download_audio", lambda url, workdir: workdir / "audio.m4a")
    monkeypatch.setattr(asr, "_run_whisper", lambda path, cfg: None)

    cfg = AsrCfg(remote_url="")
    asr.transcribe("https://youtube.com/watch?v=abc", cfg=cfg, temp_dir=None)

    assert called.get("local") is True
