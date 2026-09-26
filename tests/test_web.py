from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from datetime import UTC, datetime

import pytest

from ytdigest.db.repo import Repo
from ytdigest.locking import LockHeld
from ytdigest.models import FeedEntry, RunReport
from ytdigest.web.app import create_app
from ytdigest.web.jobs import JobRunner

# --------------------------------------------------------------- JobRunner


def test_jobrunner_broadcasts_logs_and_done(cfg, conn):
    runner = JobRunner(cfg)
    q = runner.subscribe()

    def fake_job(pipeline):
        logging.getLogger("ytdigest").info("Zwischenschritt")
        return RunReport(processed=3, via_asr=1)

    assert runner.start("Testjob", fake_job)

    items = []
    while True:
        item = q.get(timeout=5)
        items.append(item)
        if item["type"] == "done":
            break

    assert any(i["type"] == "log" and "Zwischenschritt" in i["text"] for i in items)
    assert items[-1]["report"]["processed"] == 3
    assert items[-1]["error"] is None
    assert not runner.running


def test_jobrunner_rejects_concurrent_start(cfg, conn):
    runner = JobRunner(cfg)
    release = threading.Event()

    def blocking_job(pipeline):
        release.wait(timeout=5)
        return RunReport()

    assert runner.start("Erster Job", blocking_job)
    assert runner.running
    assert runner.start("Zweiter Job", lambda p: RunReport()) is False

    release.set()
    for _ in range(50):
        if not runner.running:
            break
        time.sleep(0.05)
    assert not runner.running


def test_jobrunner_reports_lock_collision(cfg, conn, monkeypatch):
    import ytdigest.web.jobs as jobs_mod

    @contextlib.contextmanager
    def fake_single_instance(path):
        raise LockHeld("gehalten")
        yield  # pragma: no cover - contextmanager braucht formal einen yield

    monkeypatch.setattr(jobs_mod, "single_instance", fake_single_instance)
    runner = JobRunner(cfg)
    q = runner.subscribe()
    runner.start("Job", lambda p: RunReport())

    items = []
    while True:
        item = q.get(timeout=5)
        items.append(item)
        if item["type"] == "done":
            break
    assert "Lock" in items[-1]["error"]


# ------------------------------------------------------------- Flask-Routen


@pytest.fixture
def app(cfg, conn):
    flask_app = create_app(cfg)
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_feed_with_done_video(cfg, conn):
    repo = Repo(conn)
    feed = repo.create_feed(channel_id="UCtest", feed_url="https://example/feed.xml",
                            dir_slug="Test", channel_title="Test Kanal", display_name=None)
    entry = FeedEntry("vid001", "Erstes Video", datetime(2026, 1, 1, tzinfo=UTC),
                      "https://youtube.com/watch?v=vid001")
    repo.add_video(entry, feed.id, status="discovered")
    conn.execute(
        "UPDATE videos SET status='done', source='captions_auto', duration_s=900, "
        "transcript_path=?, metadata_path=? WHERE video_id='vid001'",
        ("Test/2026-01-01_000000_Erstes_Video.txt", "Test/2026-01-01_000000_Erstes_Video.json"),
    )
    target_dir = cfg.paths.output_dir / "Test"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "2026-01-01_000000_Erstes_Video.txt").write_text(
        "Hallo Welt.\n", encoding="utf-8")
    (target_dir / "2026-01-01_000000_Erstes_Video.json").write_text(
        json.dumps({"video_id": "vid001"}), encoding="utf-8")
    return feed


def test_dashboard_shows_status_counts(client, cfg, conn):
    _seed_feed_with_done_video(cfg, conn)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data
    assert b"done" in resp.data


def test_feeds_page_lists_feed(client, cfg, conn):
    _seed_feed_with_done_video(cfg, conn)
    resp = client.get("/feeds")
    assert resp.status_code == 200
    assert b"Test Kanal" in resp.data


def test_feeds_add_writes_feeds_txt_and_starts_job(app, client, cfg):
    started = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: started.setdefault("ok", True) or True
    )

    resp = client.post("/feeds/add", data={"source": "@neuerkanal", "name": ""},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert "@neuerkanal" in cfg.paths.feeds_file.read_text(encoding="utf-8")
    assert started.get("ok") is True


def test_feeds_add_rejects_invalid_source(client, cfg):
    resp = client.post("/feeds/add", data={"source": "!!!nonsense!!!"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert not cfg.paths.feeds_file.exists() or (
        "!!!nonsense!!!" not in cfg.paths.feeds_file.read_text(encoding="utf-8"))


def test_feeds_remove_keeps_other_lines(client, cfg, conn):
    feed = _seed_feed_with_done_video(cfg, conn)
    conn.execute("UPDATE feeds SET resolved_from = '@testkanal' WHERE id = ?", (feed.id,))
    cfg.paths.feeds_file.write_text(
        "# Kommentar\n@testkanal\n@anderer_kanal\n", encoding="utf-8")

    resp = client.post(f"/feeds/{feed.id}/remove", follow_redirects=True)
    assert resp.status_code == 200
    remaining = cfg.paths.feeds_file.read_text(encoding="utf-8")
    assert "@testkanal" not in remaining
    assert "@anderer_kanal" in remaining
    assert "# Kommentar" in remaining


def test_feeds_retry_requeues_videos(client, cfg, conn):
    feed = _seed_feed_with_done_video(cfg, conn)
    conn.execute(
        "UPDATE videos SET status='skipped', skip_reason='initial_sync' WHERE video_id='vid001'"
    )
    resp = client.post(f"/feeds/{feed.id}/retry", follow_redirects=True)
    assert resp.status_code == 200
    assert Repo(conn).get_video("vid001").status == "discovered"


def test_transcripts_list_and_detail_and_download(client, cfg, conn):
    _seed_feed_with_done_video(cfg, conn)

    listing = client.get("/transcripts")
    assert listing.status_code == 200
    assert b"Erstes Video" in listing.data

    detail = client.get("/transcripts/vid001")
    assert detail.status_code == 200
    assert b"Hallo Welt." in detail.data

    download = client.get("/transcripts/vid001/download/txt")
    assert download.status_code == 200
    assert b"Hallo Welt." in download.data

    missing = client.get("/transcripts/does-not-exist")
    assert missing.status_code == 404


def test_transcripts_download_rejects_path_traversal(client, cfg, conn):
    _seed_feed_with_done_video(cfg, conn)
    conn.execute(
        "UPDATE videos SET transcript_path = ? WHERE video_id = 'vid001'",
        ("../outside.txt",),
    )
    (cfg.paths.output_dir.parent / "outside.txt").write_text("geheim", encoding="utf-8")

    resp = client.get("/transcripts/vid001/download/txt")
    assert resp.status_code == 404


def test_jobs_status_and_stream_idle(client):
    status = client.get("/jobs/status")
    assert status.status_code == 200
    assert status.get_json() == {"running": False, "label": None}

    stream = client.get("/jobs/stream")
    assert stream.status_code == 200
    assert b"idle" in stream.data
