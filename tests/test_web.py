from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from datetime import UTC, datetime

import pytest

from ytdigest.db.repo import Repo
from ytdigest.feeds.fetcher import FeedFetchResult
from ytdigest.locking import LockHeld
from ytdigest.models import FeedEntry, RunReport
from ytdigest.web import transcripts_views
from ytdigest.web.app import create_app
from ytdigest.web.jobs import JobRunner
from ytdigest.web.transcripts_views import _dewrap_paragraphs

# --------------------------------------------------------------- JobRunner


def test_jobrunner_broadcasts_logs_and_done(cfg, conn):
    runner = JobRunner(cfg)
    q = runner.subscribe()

    def fake_job(pipeline):
        logging.getLogger("ytdigest").info("Zwischenschritt")
        return RunReport(processed=3, via_asr=1, processed_titles=["Folge A", "Folge B"])

    assert runner.start("Testjob", fake_job)

    items = []
    while True:
        item = q.get(timeout=5)
        items.append(item)
        if item["type"] == "done":
            break

    assert any(i["type"] == "log" and "Zwischenschritt" in i["text"] for i in items)
    assert items[-1]["report"]["processed"] == 3
    assert items[-1]["report"]["processed_titles"] == ["Folge A", "Folge B"]
    assert items[-1]["error"] is None
    assert not runner.running
    assert runner.last_report.processed_titles == ["Folge A", "Folge B"]


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


def test_dashboard_shows_no_run_yet_before_any_job(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Noch kein Lauf" in resp.data


def test_dashboard_shows_processed_titles_after_sync(app, client):
    app.extensions["ytdigest_jobs"].last_report = RunReport(
        processed=2, processed_titles=["Folge 1", "Folge 2"],
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Folge 1" in resp.data
    assert b"Folge 2" in resp.data


def test_dashboard_shows_nothing_new_when_sync_found_nothing(app, client):
    app.extensions["ytdigest_jobs"].last_report = RunReport(processed=0)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Keine neuen Transkripte" in resp.data


def test_feeds_page_lists_feed(client, cfg, conn):
    feed = _seed_feed_with_done_video(cfg, conn)
    resp = client.get("/feeds")
    assert resp.status_code == 200
    assert b"Test Kanal" in resp.data
    assert f'href="/transcripts?feed={feed.id}"'.encode() in resp.data


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


def test_dewrap_paragraphs_joins_hard_wraps_keeps_paragraph_breaks():
    # wie reflow.wrap_transcript es ablegt: pro Absatz hart auf eine Breite
    # umgebrochen, Absätze durch eine Leerzeile getrennt
    text = "Das ist die erste Zeile\ndes ersten Absatzes.\n\nUnd das hier ist\nder zweite Absatz."
    assert _dewrap_paragraphs(text) == [
        "Das ist die erste Zeile des ersten Absatzes.",
        "Und das hier ist der zweite Absatz.",
    ]


def test_dewrap_paragraphs_splits_giant_block_without_pauses():
    # captions_auto liefert manchmal gar keine Leerzeile (keine erkennbare
    # Sprechpause über die ganze Folge) - dann soll die Vorschau trotzdem in
    # lesbaren Häppchen statt einem einzigen Riesenblock erscheinen
    sentence = "Das ist ein Satz mit ungefähr fünfzig Zeichen Länge hier."
    text = " ".join([sentence] * 20)  # deutlich über der 500-Zeichen-Schwelle
    paragraphs = _dewrap_paragraphs(text)
    assert len(paragraphs) > 1
    assert all(p.strip().endswith(".") for p in paragraphs)
    assert " ".join(paragraphs) == text


def test_transcripts_detail_dewraps_hard_line_breaks(client, cfg, conn):
    _seed_feed_with_done_video(cfg, conn)
    path = cfg.paths.output_dir / "Test" / "2026-01-01_000000_Erstes_Video.txt"
    path.write_text("Zeile eins\nZeile zwei.\n\nZweiter Absatz.\n", encoding="utf-8")

    resp = client.get("/transcripts/vid001")
    assert resp.status_code == 200
    assert b"<p>Zeile eins Zeile zwei.</p>" in resp.data
    assert b"<p>Zweiter Absatz.</p>" in resp.data


def test_transcripts_detail_and_download_support_slash_in_video_id(client, cfg, conn):
    # lokale Videos (README: 'local scan') haben video_id = '<kanal>/<datei>' -
    # die Route muss den Schrägstrich im Pfadsegment durchlassen (path-Converter)
    repo = Repo(conn)
    feed = repo.create_feed(channel_id="lokal-kanal", feed_url="file:///lokal-kanal",
                            dir_slug="Lokal", channel_title="Lokal", display_name=None)
    entry = FeedEntry("Lokal/video.mp4", "Lokales Video", datetime(2026, 1, 1, tzinfo=UTC),
                      "/videos/Lokal/video.mp4")
    repo.add_video(entry, feed.id, status="discovered")
    conn.execute(
        "UPDATE videos SET status='done', transcript_path=? WHERE video_id='Lokal/video.mp4'",
        ("Lokal/video.txt",),
    )
    target_dir = cfg.paths.output_dir / "Lokal"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "video.txt").write_text("Lokaler Inhalt.\n", encoding="utf-8")

    detail = client.get("/transcripts/Lokal/video.mp4")
    assert detail.status_code == 200
    assert b"Lokaler Inhalt." in detail.data

    download = client.get("/transcripts/Lokal/video.mp4/download/txt")
    assert download.status_code == 200
    assert b"Lokaler Inhalt." in download.data


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


def test_transcripts_shows_pending_video_with_action_button(client, cfg, conn, monkeypatch):
    feed = _seed_feed_with_done_video(cfg, conn)
    entry = FeedEntry("vid002", "Zweites Video, noch offen", datetime(2026, 1, 2, tzinfo=UTC),
                      "https://youtube.com/watch?v=vid002")
    Repo(conn).add_video(entry, feed.id, status="discovered")
    monkeypatch.setattr(transcripts_views, "fetch_feed",
                        lambda *a, **k: FeedFetchResult(entries=[]))

    resp = client.get(f"/transcripts?feed={feed.id}")
    assert resp.status_code == 200
    assert b"Zweites Video, noch offen" in resp.data
    assert b"Herunterladen &amp; transkribieren" in resp.data
    assert b"/transcripts/vid002/process" in resp.data


def test_transcripts_marks_too_short_skips_with_force_button(client, cfg, conn, monkeypatch):
    # Dauer steht beim RSS-Sync noch nicht fest, daher taucht das Video zuerst
    # wie jedes andere 'noch nicht verfügbare' auf und erst nach einem
    # Verarbeitungsversuch als 'übersprungen (zu kurz)' - es soll dabei nicht
    # kommentarlos aus der Liste verschwinden (sähe wie ein stiller Fehler aus).
    # Der Button bleibt erhalten, damit sich ein zu kurzes Video trotzdem
    # gezielt erzwingen lässt.
    feed = _seed_feed_with_done_video(cfg, conn)
    repo = Repo(conn)
    repo.add_video(
        FeedEntry("short001", "Nur ein Short", datetime(2026, 1, 2, tzinfo=UTC),
                 "https://youtube.com/watch?v=short001"),
        feed.id, status="skipped", skip_reason="too_short", duration_s=45,
    )
    monkeypatch.setattr(transcripts_views, "fetch_feed",
                        lambda *a, **k: FeedFetchResult(entries=[]))

    resp = client.get(f"/transcripts?feed={feed.id}")
    assert resp.status_code == 200
    assert b"Nur ein Short" in resp.data
    assert b"/transcripts/short001/process" in resp.data


def test_transcripts_process_ignores_duration_filter(app, client, cfg, conn):
    # Ein Klick auf das 'zu kurz'-Icon soll das Video trotz Dauerfilter
    # transkribieren - process_one() bekommt dafür min_duration_min=0 mit.
    feed = _seed_feed_with_done_video(cfg, conn)
    Repo(conn).add_video(
        FeedEntry("short001", "Nur ein Short", datetime(2026, 1, 2, tzinfo=UTC),
                 "https://youtube.com/watch?v=short001"),
        feed.id, status="skipped", skip_reason="too_short", duration_s=45,
    )
    captured = {}

    def fake_start(label, func):
        captured["func"] = func
        return True

    app.extensions["ytdigest_jobs"].start = fake_start
    resp = client.post("/transcripts/short001/process")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    class _FakePipeline:
        def process_one(self, video_id, opts):
            captured["video_id"] = video_id
            captured["opts"] = opts
            return RunReport()

    captured["func"](_FakePipeline())
    assert captured["video_id"] == "short001"
    assert captured["opts"].min_duration_min == 0


def test_transcripts_quick_sync_adds_new_entries_from_live_feed(client, cfg, conn, monkeypatch):
    feed = _seed_feed_with_done_video(cfg, conn)
    new_entry = FeedEntry("brandneu", "Brandneue Folge", datetime(2026, 2, 1, tzinfo=UTC),
                         "https://youtube.com/watch?v=brandneu")
    monkeypatch.setattr(transcripts_views, "fetch_feed",
                        lambda *a, **k: FeedFetchResult(entries=[new_entry]))

    resp = client.get(f"/transcripts?feed={feed.id}")
    assert resp.status_code == 200
    assert b"Brandneue Folge" in resp.data
    video = Repo(conn).get_video("brandneu")
    assert video is not None
    assert video.status == "discovered"


def test_transcripts_quick_sync_error_keeps_page_usable(client, cfg, conn, monkeypatch):
    _seed_feed_with_done_video(cfg, conn)
    feed = Repo(conn).get_feed_by_channel_id("UCtest")
    monkeypatch.setattr(transcripts_views, "fetch_feed",
                        lambda *a, **k: FeedFetchResult(error="rate_limited (429)"))

    resp = client.get(f"/transcripts?feed={feed.id}")
    assert resp.status_code == 200
    assert b"rate_limited" in resp.data
    assert b"Erstes Video" in resp.data


def test_transcripts_process_starts_job(app, client, cfg, conn):
    _seed_feed_with_done_video(cfg, conn)
    Repo(conn).add_video(
        FeedEntry("vid002", "Zweites Video", datetime(2026, 1, 2, tzinfo=UTC),
                 "https://youtube.com/watch?v=vid002"),
        Repo(conn).get_feed_by_channel_id("UCtest").id, status="discovered",
    )
    started = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: started.setdefault("label", label) or True
    )

    resp = client.post("/transcripts/vid002/process")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert "Zweites Video" in started["label"]


def test_transcripts_process_unknown_video_404(client):
    resp = client.post("/transcripts/does-not-exist/process")
    assert resp.status_code == 404


def test_jobs_status_and_stream_idle(client):
    status = client.get("/jobs/status")
    assert status.status_code == 200
    assert status.get_json() == {"running": False, "label": None}

    stream = client.get("/jobs/stream")
    assert stream.status_code == 200
    assert b"idle" in stream.data
