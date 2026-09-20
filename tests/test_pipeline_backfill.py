from __future__ import annotations

import pytest

from ytdigest import pipeline as pl
from ytdigest.db.repo import Repo
from ytdigest.models import VideoProbe
from ytdigest.pipeline import Pipeline


def _uploads():
    # neueste zuerst, wie yt-dlp den "Videos"-Tab liefert
    return [
        {"video_id": "known0001", "title": "Bereits bekannt"},
        {"video_id": "fresh0002", "title": "Neu, innerhalb des Fensters"},
        {"video_id": "old00003", "title": "Zu alt"},
    ]


@pytest.fixture
def patched(cfg, monkeypatch):
    cfg.feeds.delay_between_s = 0
    monkeypatch.setattr(pl.channel, "list_uploads", lambda cid: _uploads())

    dates = {"fresh0002": "20260901", "old00003": "20240101"}

    def fake_probe(url):
        vid = url.rsplit("=", 1)[-1]
        return VideoProbe(duration_s=900, info={"upload_date": dates.get(vid)})

    monkeypatch.setattr(pl.captions, "probe", fake_probe)
    return monkeypatch


def _seed_feed(conn):
    repo = Repo(conn)
    feed = repo.create_feed(channel_id="UCtest", feed_url="https://example/feed.xml",
                            dir_slug="Test", channel_title="Test", display_name=None)
    conn.execute(
        "INSERT INTO videos (video_id, feed_id, title, published_at, url, status, "
        "attempts, first_seen_at, summary_status) "
        "VALUES ('known0001', ?, 'Bereits bekannt', '2026-09-10T00:00:00Z', "
        "'https://youtube.com/watch?v=known0001', 'done', 0, '2026-09-10T00:00:00Z', 'none')",
        (feed.id,),
    )
    return feed


def test_backfill_stops_at_cutoff_and_skips_known(cfg, conn, patched):
    _seed_feed(conn)
    report = Pipeline(cfg, conn).backfill_channel("UCtest", days=365)

    repo = Repo(conn)
    assert repo.video_exists("fresh0002")
    assert repo.get_video("fresh0002").status == "discovered"
    assert not repo.video_exists("old00003")
    assert report.new_videos == 1


def test_backfill_unknown_channel_raises(cfg, conn, patched):
    with pytest.raises(ValueError):
        Pipeline(cfg, conn).backfill_channel("UCdoesnotexist", days=365)


def test_backfill_dry_run_does_not_write(cfg, conn, patched):
    _seed_feed(conn)
    report = Pipeline(cfg, conn).backfill_channel("UCtest", days=365, dry_run=True)

    repo = Repo(conn)
    assert not repo.video_exists("fresh0002")
    assert report.new_videos == 1


def test_backfill_respects_limit(cfg, conn, patched, monkeypatch):
    monkeypatch.setattr(pl.channel, "list_uploads", lambda cid: [
        {"video_id": "fresh0002", "title": "A"},
        {"video_id": "fresh0003", "title": "B"},
    ])

    def fake_probe(url):
        return VideoProbe(duration_s=900, info={"upload_date": "20260901"})

    monkeypatch.setattr(pl.captions, "probe", fake_probe)
    _seed_feed(conn)
    report = Pipeline(cfg, conn).backfill_channel("UCtest", days=365, limit=1)

    assert report.new_videos == 1
