from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ytdigest.config import AsrCfg
from ytdigest.sources import asr


class _FakeSegment:
    def __init__(self, text: str, start: float, end: float) -> None:
        self.text = text
        self.start = start
        self.end = end


class _FakeWhisperModel:
    captured: dict = {}

    def __init__(self, model, device, compute_type):
        pass

    def transcribe(self, media_path, **kwargs):
        _FakeWhisperModel.captured = kwargs
        return [_FakeSegment("Hallo Welt.", 0.0, 1.0)], SimpleNamespace(language="de")


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules, "faster_whisper",
        SimpleNamespace(WhisperModel=_FakeWhisperModel),
    )
    return _FakeWhisperModel


def test_run_whisper_passes_repetition_guards(fake_model):
    cfg = AsrCfg(device="cpu")
    result = asr._run_whisper(Path("dummy.wav"), cfg)

    assert result is not None
    assert fake_model.captured["condition_on_previous_text"] is False
    assert fake_model.captured["repetition_penalty"] == 1.1
    assert fake_model.captured["no_repeat_ngram_size"] == 3
    assert fake_model.captured["beam_size"] == cfg.beam_size
