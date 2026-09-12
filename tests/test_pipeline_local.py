from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ytdigest import pipeline as pl
from ytdigest.db.repo import Repo
from ytdigest.models import FeedEntry, TranscriptResult, VideoProbe
from ytdigest.pipeline import Pipeline, RunOptions


def _make_video(root, channel: str, filename: str, content: bytes = b"x") -> None:
    channel_dir = root / channel
    channel_dir.mkdir(parents=True, exist_ok=True)
    (channel_dir / filename).write_bytes(content)


@pytest.fixture
def local_patched(cfg, monkeypatch):
    cfg.asr.enabled = True  # conftest deaktiviert ASR global; hier gebraucht
    monkeypatch.setattr(pl.local, "probe", lambda path: VideoProbe(duration_s=900))
    monkeypatch.setattr(
        pl.local, "creation_time", lambda path: datetime(2026, 1, 1, tzinfo=UTC)
    )
    monkeypatch.setattr(
        pl.asr, "transcribe_local",
        lambda path, *, cfg: TranscriptResult(
            text="ein sauberer fließtext hier", source="asr", language="de",
        ),
    )
    return monkeypatch


def test_scan_discovers_channel_per_subdir_and_transcribes(cfg, conn, local_patched, tmp_path):
    cfg.local.min_age_s = 0
    root = tmp_path / "videos"
    _make_video(root, "fom_s4_ebusiness", "lecture1.mp4")

    report = Pipeline(cfg, conn).run_local(root, RunOptions())

    assert report.new_videos == 1
    assert report.processed == 1
    assert report.via_asr == 1

    repo = Repo(conn)
    video = repo.get_video("fom_s4_ebusiness/lecture1.mp4")
    assert video.status == "done"
    assert video.source == "asr"

    feed = repo.get_feed_by_channel_id("fom_s4_ebusiness")
    assert feed is not None
    assert feed.dir_slug == "fom_s4_ebusiness"

    txt = cfg.paths.output_dir / "fom_s4_ebusiness"
    assert list(txt.glob("*.txt"))


def test_scan_skips_fresh_files(cfg, conn, local_patched, tmp_path):
    cfg.local.min_age_s = 999999  # "gerade erst geschrieben" simulieren
    root = tmp_path / "videos"
    _make_video(root, "kanal_a", "clip.mp4")

    report = Pipeline(cfg, conn).run_local(root, RunOptions())

    assert report.new_videos == 0
    assert report.processed == 0
    assert not Repo(conn).video_exists("kanal_a/clip.mp4")
    # der Kanal-Feed selbst wird trotzdem angelegt
    assert Repo(conn).get_feed_by_channel_id("kanal_a") is not None


def test_scan_second_run_finds_no_new_videos(cfg, conn, local_patched, tmp_path):
    cfg.local.min_age_s = 0
    root = tmp_path / "videos"
    _make_video(root, "kanal_a", "clip.mp4")

    Pipeline(cfg, conn).run_local(root, RunOptions())
    report = Pipeline(cfg, conn).run_local(root, RunOptions())

    assert report.new_videos == 0


def test_delete_after_success_removes_source_file(cfg, conn, local_patched, tmp_path):
    cfg.local.min_age_s = 0
    root = tmp_path / "videos"
    _make_video(root, "kanal_a", "clip.mp4")
    video_path = root / "kanal_a" / "clip.mp4"

    Pipeline(cfg, conn).run_local(root, RunOptions(delete_source_on_success=True))

    assert not video_path.exists()
    assert Repo(conn).get_video("kanal_a/clip.mp4").status == "done"


def test_keeps_source_file_by_default(cfg, conn, local_patched, tmp_path):
    cfg.local.min_age_s = 0
    root = tmp_path / "videos"
    _make_video(root, "kanal_a", "clip.mp4")
    video_path = root / "kanal_a" / "clip.mp4"

    Pipeline(cfg, conn).run_local(root, RunOptions())

    assert video_path.exists()


def test_local_scan_never_touches_unrelated_feed_videos(cfg, conn, local_patched, tmp_path):
    """run_local darf nur die unter root gefundenen Kanäle verarbeiten, keine
    anderen offen liegenden (z. B. YouTube-)Videos."""
    repo = Repo(conn)
    other_feed = repo.create_feed(
        channel_id="UCother", feed_url="https://example.com/feed.xml",
        dir_slug="other", channel_title="Other", display_name=None,
    )
    repo.add_video(
        FeedEntry("yt1", "Anderes Video", datetime(2026, 1, 1, tzinfo=UTC),
                  "https://youtube.com/watch?v=yt1"),
        other_feed.id, status="discovered",
    )

    root = tmp_path / "videos"
    cfg.local.min_age_s = 0
    Pipeline(cfg, conn).run_local(root, RunOptions())  # keine lokalen Kanäle vorhanden

    assert repo.get_video("yt1").status == "discovered"
