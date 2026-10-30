"""Tests für sources/local_remote.py - Client für per ASR-Worker freigegebene
Ordner (siehe asr_worker.py). EXPERIMENTELL/eigenständig, siehe Moduldoc."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from ytdigest.sources import local_remote


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict) -> None:
        self.status_code = status_code
        self._json = json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self) -> dict:
        return self._json


def test_feed_url_for_and_is_remote_local_feed():
    url = local_remote.feed_url_for("http://pc.local:8765/folders/fom_s4")
    assert url == "local-remote:http://pc.local:8765/folders/fom_s4"
    assert local_remote.is_remote_local_feed(url)
    assert not local_remote.is_remote_local_feed("local:/some/path")


def test_folder_id_from_url():
    assert local_remote.folder_id_from_url("http://pc.local:8765/folders/fom_s4") == "fom_s4"
    assert local_remote.folder_id_from_url("http://pc.local:8765/folders/fom_s4/") == "fom_s4"


def test_file_url_for():
    assert (local_remote.file_url_for("http://pc.local:8765/folders/fom_s4", "a.mp3")
            == "http://pc.local:8765/folders/fom_s4/files/a.mp3")


def test_list_files_success(monkeypatch):
    monkeypatch.setattr(
        local_remote.httpx, "get",
        lambda url, timeout: _FakeResponse(200, {"files": [{"filename": "a.mp3"}]}),
    )
    files = local_remote.list_files("http://pc.local:8765/folders/fom_s4")
    assert files == [{"filename": "a.mp3"}]


def test_list_files_unknown_folder_returns_empty(monkeypatch):
    monkeypatch.setattr(local_remote.httpx, "get",
                        lambda url, timeout: _FakeResponse(404, {}))
    assert local_remote.list_files("http://pc.local:8765/folders/missing") == []


def test_list_files_unreachable_raises(monkeypatch):
    def fake_get(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(local_remote.httpx, "get", fake_get)
    with pytest.raises(local_remote.RemoteFolderUnavailable):
        local_remote.list_files("http://pc.local:8765/folders/fom_s4")


def test_probe_success(monkeypatch):
    monkeypatch.setattr(local_remote.httpx, "get",
                        lambda url, timeout: _FakeResponse(200, {"duration_s": 900}))
    result = local_remote.probe("http://pc.local:8765/folders/fom_s4/files/a.mp3")
    assert result.duration_s == 900
    assert result.error is None


def test_probe_missing_file(monkeypatch):
    monkeypatch.setattr(local_remote.httpx, "get",
                        lambda url, timeout: _FakeResponse(404, {}))
    result = local_remote.probe("http://pc.local:8765/folders/fom_s4/files/gone.mp3")
    assert result.error == "missing"


def test_read_existing_transcript_found(monkeypatch):
    monkeypatch.setattr(
        local_remote.httpx, "get",
        lambda url, timeout: _FakeResponse(
            200, {"text": "hallo", "source": "local_txt", "language": ""}),
    )
    result = local_remote.read_existing_transcript("http://pc.local:8765/folders/fom_s4/files/a.mp3")
    assert result.text == "hallo"
    assert result.source == "local_txt"


def test_read_existing_transcript_none(monkeypatch):
    monkeypatch.setattr(local_remote.httpx, "get",
                        lambda url, timeout: _FakeResponse(404, {}))
    assert local_remote.read_existing_transcript(
        "http://pc.local:8765/folders/fom_s4/files/a.mp3") is None


def test_transcribe_success(monkeypatch):
    monkeypatch.setattr(
        local_remote.httpx, "post",
        lambda url, timeout: _FakeResponse(
            200, {"text": "Text", "source": "asr", "language": "de", "asr_model": "large-v3"}),
    )
    result = local_remote.transcribe("http://pc.local:8765/folders/fom_s4/files/a.mp3",
                                     timeout_s=30)
    assert result.text == "Text"
    assert result.asr_model == "large-v3"


def test_transcribe_503_raises_asr_unavailable(monkeypatch):
    monkeypatch.setattr(
        local_remote.httpx, "post",
        lambda url, timeout: _FakeResponse(503, {"error": "ASR fehlt"}),
    )
    from ytdigest.sources.asr import AsrUnavailable
    with pytest.raises(AsrUnavailable):
        local_remote.transcribe("http://pc.local:8765/folders/fom_s4/files/a.mp3", timeout_s=30)


def test_transcribe_unreachable_raises(monkeypatch):
    def fake_post(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(local_remote.httpx, "post", fake_post)
    with pytest.raises(local_remote.RemoteFolderUnavailable):
        local_remote.transcribe("http://pc.local:8765/folders/fom_s4/files/a.mp3", timeout_s=30)


def test_parse_created_at():
    assert local_remote.parse_created_at("2026-01-02T03:04:05Z") == datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=UTC)
