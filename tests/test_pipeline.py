from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ytdigest import pipeline as pl
from ytdigest.db.repo import Repo
from ytdigest.feeds.fetcher import FeedFetchResult
from ytdigest.models import FeedEntry, TranscriptResult, VideoProbe
from ytdigest.pipeline import Pipeline, RunOptions


def _entries():
    return [
        FeedEntry("newvideo0001", "Neues langes Video",
                  datetime(2026, 8, 30, tzinfo=UTC),
                  "https://youtube.com/watch?v=newvideo0001"),
        FeedEntry("oldvideo0002", "Alter Short",
                  datetime(2026, 8, 20, tzinfo=UTC),
                  "https://youtube.com/watch?v=oldvideo0002"),
    ]


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(pl, "fetch_feed", lambda *a, **k: FeedFetchResult(
        entries=_entries(), channel_id="UCbRP3c757lWg9M-U7TyEkXA",
        channel_title="Theo - t3.gg", etag='"abc"'))

    durations = {"newvideo0001": 900, "oldvideo0002": 120}

    def fake_probe(url):
        vid = url.rsplit("=", 1)[-1]
        return VideoProbe(duration_s=durations.get(vid, 900), info={})

    monkeypatch.setattr(pl.captions, "probe", fake_probe)
    monkeypatch.setattr(pl.captions, "fetch_captions", lambda *a, **k: TranscriptResult(
        text="ein sauberer fließtext hier", source="captions_auto", language="de"))
    return monkeypatch


def test_initial_mode_latest_picks_first_long_video(cfg, conn, patched):
    cfg.paths.feeds_file.write_text("UCbRP3c757lWg9M-U7TyEkXA | Theo\n", encoding="utf-8")
    report = Pipeline(cfg, conn).run(RunOptions(initial_mode="latest"))

    repo = Repo(conn)
    assert repo.get_video("newvideo0001").status == "done"
    assert repo.get_video("oldvideo0002").status == "skipped"
    assert report.processed == 1
    assert report.via_captions == 1

    txt = cfg.paths.output_dir / "Theo" / "2026-08-30_000000_Neues_langes_Video.txt"
    assert txt.exists()
    assert txt.read_bytes().endswith(b"\n")
    assert txt.with_suffix(".json").exists()


def test_initial_mode_mark_seen_skips_all(cfg, conn, patched):
    cfg.paths.feeds_file.write_text("UCbRP3c757lWg9M-U7TyEkXA\n", encoding="utf-8")
    Pipeline(cfg, conn).run(RunOptions(initial_mode="mark-seen", sync_only=True))
    repo = Repo(conn)
    assert repo.get_video("newvideo0001").status == "skipped"
    assert repo.get_video("oldvideo0002").status == "skipped"


def test_second_run_only_processes_new(cfg, conn, patched):
    cfg.paths.feeds_file.write_text("UCbRP3c757lWg9M-U7TyEkXA\n", encoding="utf-8")
    Pipeline(cfg, conn).run(RunOptions(initial_mode="mark-seen"))
    # danach ist last_checked_at gesetzt -> kein Erstlauf mehr
    report = Pipeline(cfg, conn).run(RunOptions())
    assert report.new_videos == 0


def test_failed_video_gets_retry_time(cfg, conn, patched, monkeypatch):
    cfg.paths.feeds_file.write_text("UCbRP3c757lWg9M-U7TyEkXA\n", encoding="utf-8")

    def boom(url):
        return VideoProbe(duration_s=None, error="private")

    Pipeline(cfg, conn).run(RunOptions(initial_mode="backfill", sync_only=True))
    monkeypatch.setattr(pl.captions, "probe", boom)
    report = Pipeline(cfg, conn).run(RunOptions())

    repo = Repo(conn)
    video = repo.get_video("newvideo0001")
    assert video.status == "failed"
    assert video.last_error == "private"
    assert video.next_retry_at is not None
    assert report.failed >= 1


def test_processing_reset_between_runs(cfg, conn, patched):
    cfg.paths.feeds_file.write_text("UCbRP3c757lWg9M-U7TyEkXA\n", encoding="utf-8")
    Pipeline(cfg, conn).run(RunOptions(initial_mode="backfill", sync_only=True))
    conn.execute("UPDATE videos SET status='processing' WHERE video_id='newvideo0001'")
    Pipeline(cfg, conn).run(RunOptions(sync_only=True))
    assert Repo(conn).get_video("newvideo0001").status == "discovered"
