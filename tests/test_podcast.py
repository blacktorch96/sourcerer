"""Tests für sources/podcast.py - bislang nur der Remote-Zweig (cfg.remote_url),
siehe asr_worker.py."""

from __future__ import annotations

from ytdigest.config import AsrCfg
from ytdigest.sources import asr, podcast


def test_transcribe_remote_delegates_to_asr_remote_with_podcast_kind(monkeypatch):
    captured = {}

    def fake_remote_transcribe(url, *, kind, cfg):
        captured["url"] = url
        captured["kind"] = kind
        return "RESULT"

    monkeypatch.setattr(asr, "_remote_transcribe", fake_remote_transcribe)
    cfg = AsrCfg(remote_url="http://pc.local:8765")

    result = podcast.transcribe("https://example.com/episode.mp3", cfg=cfg, temp_dir=None)

    assert result == "RESULT"
    assert captured == {"url": "https://example.com/episode.mp3", "kind": "podcast"}


def test_transcribe_without_remote_url_downloads_locally(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(asr, "_require_faster_whisper", lambda: called.setdefault("checked", True))
    monkeypatch.setattr(podcast, "_download_audio", lambda url, workdir: workdir / "episode.mp3")
    monkeypatch.setattr(asr, "transcribe_local", lambda path, *, cfg: "LOCAL_RESULT")

    cfg = AsrCfg(remote_url="")
    result = podcast.transcribe("https://example.com/episode.mp3", cfg=cfg, temp_dir=str(tmp_path))

    assert result == "LOCAL_RESULT"
    assert called.get("checked") is True
