"""Tests für web/summary_views.py (EXPERIMENTELL, siehe dortige Moduldoc) -
bewusst in einer eigenen Datei, damit sie beim Entfernen des Features mit
entfernt werden kann, ohne die übrigen Web-Tests anzufassen."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ytdigest.db.repo import Repo
from ytdigest.models import FeedEntry, SummaryResult
from ytdigest.web import summary_views
from ytdigest.web.app import create_app


@pytest.fixture
def app(cfg, conn):
    flask_app = create_app(cfg)
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_done_video(cfg, conn, video_id="vid001", title="Erstes Video"):
    repo = Repo(conn)
    feed = repo.create_feed(channel_id="UCtest", feed_url="https://example/feed.xml",
                            dir_slug="Test", channel_title="Test Kanal", display_name=None)
    entry = FeedEntry(video_id, title, datetime(2026, 1, 1, tzinfo=UTC),
                      f"https://youtube.com/watch?v={video_id}")
    repo.add_video(entry, feed.id, status="discovered")
    transcript_rel = f"Test/{video_id}.txt"
    conn.execute(
        "UPDATE videos SET status='done', transcript_path=? WHERE video_id=?",
        (transcript_rel, video_id),
    )
    target_dir = cfg.paths.output_dir / "Test"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{video_id}.txt").write_text("Ein Transkript.", encoding="utf-8")
    return feed, transcript_rel


def test_get_summary_state_empty_without_files(cfg, conn, app):
    with app.app_context():
        assert summary_views.get_summary_state(None) == summary_views.SummaryState()
        assert summary_views.get_summary_state("Test/missing.txt") == summary_views.SummaryState()


def test_get_summary_state_reads_existing_summary(cfg, conn, app):
    target = cfg.paths.output_dir / "Test"
    target.mkdir(parents=True, exist_ok=True)
    (target / "vid001.summary.md").write_text("Kurzfassung.", encoding="utf-8")
    with app.app_context():
        state = summary_views.get_summary_state("Test/vid001.txt")
    assert state.text == "Kurzfassung."
    assert state.error is None


def test_get_summary_state_reads_error_when_no_summary(cfg, conn, app):
    target = cfg.paths.output_dir / "Test"
    target.mkdir(parents=True, exist_ok=True)
    (target / "vid001.summary.error.txt").write_text("Ollama nicht erreichbar", encoding="utf-8")
    with app.app_context():
        state = summary_views.get_summary_state("Test/vid001.txt")
    assert state.error == "Ollama nicht erreichbar"
    assert state.text is None


def test_get_summary_state_prefers_summary_over_stale_error(cfg, conn, app):
    target = cfg.paths.output_dir / "Test"
    target.mkdir(parents=True, exist_ok=True)
    (target / "vid001.summary.md").write_text("Neue Zusammenfassung.", encoding="utf-8")
    (target / "vid001.summary.error.txt").write_text("alter Fehler", encoding="utf-8")
    with app.app_context():
        state = summary_views.get_summary_state("Test/vid001.txt")
    assert state.text == "Neue Zusammenfassung."


def test_summarize_route_success_writes_summary_and_clears_error(
    app, client, cfg, conn, monkeypatch,
):
    _seed_done_video(cfg, conn)
    target = cfg.paths.output_dir / "Test"
    error_file = target / "vid001.summary.error.txt"
    error_file.write_text("alter Fehler", encoding="utf-8")

    monkeypatch.setattr(summary_views.ollama_summarize, "summarize",
                        lambda text, cfg: SummaryResult(text="Kurz gesagt: alles gut."))
    captured = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: captured.setdefault("func", func) or True
    )

    resp = client.post("/transcripts/vid001/summarize")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    captured["func"](None)  # führt den Job synchron aus, wie es JobRunner täte

    summary_file = target / "vid001.summary.md"
    assert summary_file.read_text(encoding="utf-8").strip() == "Kurz gesagt: alles gut."
    assert not error_file.exists()


def test_summarize_route_overwrites_existing_summary(app, client, cfg, conn, monkeypatch):
    # 'Neu generieren' auf der Detailseite muss eine bereits vorhandene
    # Zusammenfassung überschreiben können, nicht nur eine fehlende anlegen
    _seed_done_video(cfg, conn)
    summary_file = cfg.paths.output_dir / "Test" / "vid001.summary.md"
    summary_file.write_text("Alte Zusammenfassung.", encoding="utf-8")

    monkeypatch.setattr(summary_views.ollama_summarize, "summarize",
                        lambda text, cfg: SummaryResult(text="Neue Zusammenfassung."))
    captured = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: captured.setdefault("func", func) or True
    )

    resp = client.post("/transcripts/vid001/summarize")
    assert resp.status_code == 200
    captured["func"](None)

    assert summary_file.read_text(encoding="utf-8").strip() == "Neue Zusammenfassung."


def test_summarize_route_failure_writes_error_file(app, client, cfg, conn, monkeypatch):
    _seed_done_video(cfg, conn)
    monkeypatch.setattr(
        summary_views.ollama_summarize, "summarize",
        lambda text, cfg: SummaryResult(text=None, error="Ollama nicht erreichbar"),
    )
    captured = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: captured.setdefault("func", func) or True
    )

    resp = client.post("/transcripts/vid001/summarize")
    assert resp.status_code == 200
    captured["func"](None)

    error_file = cfg.paths.output_dir / "Test" / "vid001.summary.error.txt"
    assert error_file.read_text(encoding="utf-8") == "Ollama nicht erreichbar"


def test_summarize_route_404_for_unknown_video(client):
    resp = client.post("/transcripts/does-not-exist/summarize")
    assert resp.status_code == 404


def test_summarize_route_409_when_job_running(app, client, cfg, conn):
    _seed_done_video(cfg, conn)
    app.extensions["ytdigest_jobs"].start = lambda label, func: False
    resp = client.post("/transcripts/vid001/summarize")
    assert resp.status_code == 409


def test_transcript_detail_shows_summarize_form(client, cfg, conn):
    _seed_done_video(cfg, conn)
    resp = client.get("/transcripts/vid001")
    assert resp.status_code == 200
    assert b"/transcripts/vid001/summarize" in resp.data
    assert b"Zusammenfassung erstellen" in resp.data


def test_transcripts_list_shows_summary_icon_only_when_present(client, cfg, conn):
    _seed_done_video(cfg, conn)
    listing = client.get("/transcripts")
    assert listing.status_code == 200
    assert b"Zusammenfassung vorhanden" not in listing.data

    (cfg.paths.output_dir / "Test" / "vid001.summary.md").write_text(
        "Kurzfassung.", encoding="utf-8")
    listing = client.get("/transcripts")
    assert b"Zusammenfassung vorhanden" in listing.data
