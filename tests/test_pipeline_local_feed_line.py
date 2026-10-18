from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ytdigest import pipeline as pl
from ytdigest.db.repo import Repo
from ytdigest.models import TranscriptResult, VideoProbe
from ytdigest.pipeline import Pipeline, RunOptions


@pytest.fixture
def local_patched(cfg, monkeypatch):
    cfg.asr.enabled = True  # conftest deaktiviert ASR global; hier gebraucht
    monkeypatch.setattr(pl.local, "probe", lambda path: VideoProbe(duration_s=900))
    monkeypatch.setattr(
        pl.local, "creation_time", lambda path: datetime(2026, 1, 1, tzinfo=UTC)
    )
    asr_calls: list[str] = []

    def _fake_transcribe(path, *, cfg):
        asr_calls.append(str(path))
        return TranscriptResult(text="ein transkribierter text", source="asr", language="de")

    monkeypatch.setattr(pl.asr, "transcribe_local", _fake_transcribe)
    monkeypatch.asr_calls = asr_calls
    return monkeypatch


def _write_feeds_txt(cfg, line: str) -> None:
    cfg.paths.feeds_file.write_text(line + "\n", encoding="utf-8")


def test_single_folder_as_feed_is_discovered_and_transcribed(cfg, conn, local_patched, tmp_path):
    cfg.local.min_age_s = 0
    folder = tmp_path / "aufnahmen"
    folder.mkdir()
    (folder / "episode1.mp3").write_bytes(b"x")
    _write_feeds_txt(cfg, f"local:{folder}")

    report = Pipeline(cfg, conn).run(RunOptions())

    assert report.new_videos == 1
    assert report.processed == 1
    assert report.via_asr == 1
    assert local_patched.asr_calls

    video = Repo(conn).get_video(f"{folder.name}/episode1.mp3")
    assert video.status == "done"
    assert video.source == "asr"


def test_folder_feed_display_name_used_as_feed_name(cfg, conn, local_patched, tmp_path):
    cfg.local.min_age_s = 0
    folder = tmp_path / "aufnahmen"
    folder.mkdir()
    _write_feeds_txt(cfg, f"local:{folder} | Meine Aufnahmen")

    Pipeline(cfg, conn).run(RunOptions())

    feed = Repo(conn).get_feed_by_channel_id(folder.name)
    assert feed is not None
    assert feed.name == "Meine Aufnahmen"


def test_sibling_txt_adopted_without_asr(cfg, conn, local_patched, tmp_path):
    cfg.local.min_age_s = 0
    folder = tmp_path / "aufnahmen"
    folder.mkdir()
    (folder / "episode1.mp3").write_bytes(b"x")
    (folder / "episode1.txt").write_text("bereits vorhandenes transkript", encoding="utf-8")
    _write_feeds_txt(cfg, f"local:{folder}")

    report = Pipeline(cfg, conn).run(RunOptions())

    assert report.processed == 1
    assert report.via_asr == 0
    assert not local_patched.asr_calls  # ASR wurde nie aufgerufen

    video = Repo(conn).get_video(f"{folder.name}/episode1.mp3")
    assert video.status == "done"
    assert video.source == "local_txt"

    dir_slug = Repo(conn).get_feed_by_channel_id(folder.name).dir_slug
    txt_files = list((cfg.paths.output_dir / dir_slug).glob("*.txt"))
    assert txt_files
    assert txt_files[0].read_text(encoding="utf-8").strip() == "bereits vorhandenes transkript"


def test_sibling_txt_adopted_even_if_too_short(cfg, conn, local_patched, tmp_path, monkeypatch):
    cfg.local.min_age_s = 0
    cfg.filters.min_duration_min = 100  # absichtlich hoch, Probe liefert nur 5s
    monkeypatch.setattr(pl.local, "probe", lambda path: VideoProbe(duration_s=5))
    folder = tmp_path / "aufnahmen"
    folder.mkdir()
    (folder / "episode1.mp3").write_bytes(b"x")
    (folder / "episode1.txt").write_text("kurzes, aber vorhandenes transkript", encoding="utf-8")
    _write_feeds_txt(cfg, f"local:{folder}")

    report = Pipeline(cfg, conn).run(RunOptions())

    assert report.skipped == 0
    assert report.processed == 1
    video = Repo(conn).get_video(f"{folder.name}/episode1.mp3")
    assert video.status == "done"
    assert video.source == "local_txt"


def test_missing_folder_marks_feed_failed_without_crash(cfg, conn, local_patched, tmp_path):
    missing = tmp_path / "does-not-exist"
    _write_feeds_txt(cfg, f"local:{missing}")

    report = Pipeline(cfg, conn).run(RunOptions())

    assert report.feeds_failed == 1
    assert report.new_videos == 0
