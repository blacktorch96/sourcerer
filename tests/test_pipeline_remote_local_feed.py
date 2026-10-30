"""Pipeline-Integrationstests für 'local:<http-url>'-Zeilen in feeds.txt -
Ordner-Feeds, die über einen ASR-Worker auf einem anderen Rechner freigegeben
sind (siehe asr_worker.py, sources/local_remote.py). sources.local_remote
selbst wird gemockt - die HTTP-Mechanik ist in test_local_remote.py
abgedeckt, hier geht es nur um die Pipeline-Verdrahtung."""

from __future__ import annotations

import pytest

from ytdigest import pipeline as pl
from ytdigest.db.repo import Repo
from ytdigest.models import TranscriptResult, VideoProbe
from ytdigest.pipeline import Pipeline, RunOptions
from ytdigest.sources import local_remote

FOLDER_URL = "http://pc.local:8765/folders/fom_s4"


def _write_feeds_txt(cfg, line: str) -> None:
    cfg.paths.feeds_file.write_text(line + "\n", encoding="utf-8")


@pytest.fixture
def remote_patched(cfg, monkeypatch):
    cfg.asr.enabled = True  # conftest deaktiviert ASR global; hier gebraucht
    monkeypatch.setattr(pl.local_remote, "probe", lambda url: VideoProbe(duration_s=900))
    transcribe_calls: list[str] = []

    def _fake_transcribe(file_url, *, timeout_s):
        transcribe_calls.append(file_url)
        return TranscriptResult(text="transkribiert", source="asr", language="de")

    monkeypatch.setattr(pl.local_remote, "transcribe", _fake_transcribe)
    monkeypatch.setattr(pl.local_remote, "read_existing_transcript", lambda url: None)
    monkeypatch.transcribe_calls = transcribe_calls
    return monkeypatch


def test_remote_folder_feed_discovered_and_transcribed(cfg, conn, remote_patched):
    _write_feeds_txt(cfg, f"local:{FOLDER_URL} | FOM S4")
    remote_patched.setattr(
        pl.local_remote, "list_files",
        lambda url: [{"filename": "lecture1.mp3",
                      "created_at": "2026-01-02T03:04:05Z"}],
    )

    report = Pipeline(cfg, conn).run(RunOptions())

    assert report.new_videos == 1
    assert report.processed == 1
    assert report.via_asr == 1
    assert remote_patched.transcribe_calls == [f"{FOLDER_URL}/files/lecture1.mp3"]

    video = Repo(conn).get_video("fom_s4/lecture1.mp3")
    assert video.status == "done"
    assert video.source == "asr"
    assert video.published_at == "2026-01-02T03:04:05Z"

    feed = Repo(conn).get_feed_by_channel_id("fom_s4")
    assert feed.name == "FOM S4"


def test_remote_folder_feed_adopts_existing_txt_without_asr(cfg, conn, remote_patched):
    _write_feeds_txt(cfg, f"local:{FOLDER_URL}")
    remote_patched.setattr(
        pl.local_remote, "list_files",
        lambda url: [{"filename": "lecture1.mp3", "created_at": "2026-01-02T03:04:05Z"}],
    )
    remote_patched.setattr(
        pl.local_remote, "read_existing_transcript",
        lambda url: TranscriptResult(text="schon da", source="local_txt", language=""),
    )

    report = Pipeline(cfg, conn).run(RunOptions())

    assert report.processed == 1
    assert report.via_asr == 0
    assert remote_patched.transcribe_calls == []

    video = Repo(conn).get_video("fom_s4/lecture1.mp3")
    assert video.source == "local_txt"


def test_remote_folder_unreachable_marks_feed_failed(cfg, conn, remote_patched):
    _write_feeds_txt(cfg, f"local:{FOLDER_URL}")

    def _boom(url):
        raise local_remote.RemoteFolderUnavailable("PC ist aus")

    remote_patched.setattr(pl.local_remote, "list_files", _boom)

    report = Pipeline(cfg, conn).run(RunOptions())

    assert report.feeds_failed == 1
    assert report.new_videos == 0


def test_remote_transcribe_unreachable_becomes_retryable_failure(cfg, conn, remote_patched):
    _write_feeds_txt(cfg, f"local:{FOLDER_URL}")
    remote_patched.setattr(
        pl.local_remote, "list_files",
        lambda url: [{"filename": "lecture1.mp3", "created_at": "2026-01-02T03:04:05Z"}],
    )

    def _boom(file_url, *, timeout_s):
        raise local_remote.RemoteFolderUnavailable("PC ist aus")

    remote_patched.setattr(pl.local_remote, "transcribe", _boom)

    report = Pipeline(cfg, conn).run(RunOptions())

    assert report.failed == 1
    video = Repo(conn).get_video("fom_s4/lecture1.mp3")
    assert video.status == "failed"
    assert video.next_retry_at is not None  # wird automatisch erneut versucht
