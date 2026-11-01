"""Tests für web/summary_views.py (EXPERIMENTELL, siehe dortige Moduldoc) -
bewusst in einer eigenen Datei, damit sie beim Entfernen des Features mit
entfernt werden kann, ohne die übrigen Web-Tests anzufassen."""

from __future__ import annotations

import json
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


def test_ask_route_starts_job_with_question(app, client, cfg, conn, monkeypatch):
    _seed_done_video(cfg, conn)
    monkeypatch.setattr(summary_views.ollama_summarize, "ask",
                        lambda text, question, cfg: SummaryResult(text="42."))
    captured = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: captured.setdefault("func", func) or True
    )

    resp = client.post("/transcripts/vid001/ask", data={"question": "Wie lautet die Antwort?"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert captured["func"] is not None


def test_ask_route_rejects_empty_question(client, cfg, conn):
    _seed_done_video(cfg, conn)
    resp = client.post("/transcripts/vid001/ask", data={"question": "   "})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_ask_route_404_for_unknown_video(client):
    resp = client.post("/transcripts/does-not-exist/ask", data={"question": "Frage?"})
    assert resp.status_code == 404


def test_ask_route_409_when_job_running(app, client, cfg, conn):
    _seed_done_video(cfg, conn)
    app.extensions["ytdigest_jobs"].start = lambda label, func: False
    resp = client.post("/transcripts/vid001/ask", data={"question": "Frage?"})
    assert resp.status_code == 409


def test_ask_route_logs_question_and_answer(app, client, cfg, conn, monkeypatch, caplog):
    _seed_done_video(cfg, conn)
    monkeypatch.setattr(summary_views.ollama_summarize, "ask",
                        lambda text, question, cfg: SummaryResult(text="Die Antwort."))
    captured = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: captured.setdefault("func", func) or True
    )

    resp = client.post("/transcripts/vid001/ask", data={"question": "Worum geht's?"})
    assert resp.status_code == 200

    with caplog.at_level("INFO", logger="ytdigest.web.summary_views"):
        captured["func"](None)  # führt den Job synchron aus, wie es JobRunner täte

    assert any("Worum geht's?" in r.message for r in caplog.records)
    assert any("Die Antwort." in r.message for r in caplog.records)


def test_ask_route_logs_warning_on_failure(app, client, cfg, conn, monkeypatch, caplog):
    _seed_done_video(cfg, conn)
    monkeypatch.setattr(
        summary_views.ollama_summarize, "ask",
        lambda text, question, cfg: SummaryResult(text=None, error="Ollama nicht erreichbar"),
    )
    captured = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: captured.setdefault("func", func) or True
    )

    client.post("/transcripts/vid001/ask", data={"question": "Frage?"})
    with caplog.at_level("WARNING", logger="ytdigest.web.summary_views"):
        captured["func"](None)

    assert any("Ollama nicht erreichbar" in r.message for r in caplog.records)


def test_transcript_detail_shows_ask_form(client, cfg, conn):
    _seed_done_video(cfg, conn)
    resp = client.get("/transcripts/vid001")
    assert resp.status_code == 200
    assert b"/transcripts/vid001/ask" in resp.data
    assert b"Frage zum Transkript" in resp.data


def test_transcript_detail_hides_question_history_when_empty(client, cfg, conn):
    _seed_done_video(cfg, conn)
    resp = client.get("/transcripts/vid001")
    assert resp.status_code == 200
    assert b"qa-list" not in resp.data


def _questions_json_path(cfg, video_id="vid001"):
    return cfg.paths.output_dir / "Test" / f"{video_id}.questions.json"


def test_ask_route_persists_answer_to_questions_json(app, client, cfg, conn, monkeypatch):
    _seed_done_video(cfg, conn)
    monkeypatch.setattr(summary_views.ollama_summarize, "ask",
                        lambda text, question, cfg: SummaryResult(text="42."))
    captured = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: captured.setdefault("func", func) or True
    )

    client.post("/transcripts/vid001/ask", data={"question": "Wie lautet die Antwort?"})
    captured["func"](None)  # führt den Job synchron aus, wie es JobRunner täte

    data = json.loads(_questions_json_path(cfg).read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["question"] == "Wie lautet die Antwort?"
    assert data[0]["answer"] == "42."
    assert data[0]["error"] is None
    assert data[0]["id"]


def test_ask_route_persists_error_when_ollama_fails(app, client, cfg, conn, monkeypatch):
    _seed_done_video(cfg, conn)
    monkeypatch.setattr(
        summary_views.ollama_summarize, "ask",
        lambda text, question, cfg: SummaryResult(text=None, error="nicht erreichbar"),
    )
    captured = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: captured.setdefault("func", func) or True
    )

    client.post("/transcripts/vid001/ask", data={"question": "Frage?"})
    captured["func"](None)

    data = json.loads(_questions_json_path(cfg).read_text(encoding="utf-8"))
    assert data[0]["answer"] is None
    assert data[0]["error"] == "nicht erreichbar"


def test_ask_route_appends_to_existing_question_history(app, client, cfg, conn, monkeypatch):
    _seed_done_video(cfg, conn)
    _questions_json_path(cfg).write_text(json.dumps([
        {"id": "first001", "question": "Erste Frage", "answer": "Erste Antwort",
         "error": None, "created_at": "2026-01-01T10:00:00+00:00"},
    ]), encoding="utf-8")
    monkeypatch.setattr(summary_views.ollama_summarize, "ask",
                        lambda text, question, cfg: SummaryResult(text="Zweite Antwort"))
    captured = {}
    app.extensions["ytdigest_jobs"].start = (
        lambda label, func: captured.setdefault("func", func) or True
    )

    client.post("/transcripts/vid001/ask", data={"question": "Zweite Frage"})
    captured["func"](None)

    data = json.loads(_questions_json_path(cfg).read_text(encoding="utf-8"))
    assert [e["question"] for e in data] == ["Erste Frage", "Zweite Frage"]


def test_transcript_detail_shows_saved_questions(client, cfg, conn):
    _seed_done_video(cfg, conn)
    _questions_json_path(cfg).write_text(json.dumps([
        {"id": "abc12345", "question": "Worum geht es?", "answer": "Um die Sache.",
         "error": None, "created_at": "2026-01-02T10:00:00+00:00"},
    ]), encoding="utf-8")

    resp = client.get("/transcripts/vid001")
    assert resp.status_code == 200
    assert b"Worum geht es?" in resp.data
    assert b"Um die Sache." in resp.data
    assert b"/transcripts/vid001/questions/abc12345/delete" in resp.data


def test_transcript_detail_shows_failed_question_error(client, cfg, conn):
    _seed_done_video(cfg, conn)
    _questions_json_path(cfg).write_text(json.dumps([
        {"id": "abc12345", "question": "Wer?", "answer": None,
         "error": "Ollama nicht erreichbar", "created_at": "2026-01-02T10:00:00+00:00"},
    ]), encoding="utf-8")

    resp = client.get("/transcripts/vid001")
    assert resp.status_code == 200
    assert b"Fehlgeschlagen: Ollama nicht erreichbar" in resp.data


def test_delete_question_removes_only_targeted_entry(client, cfg, conn):
    _seed_done_video(cfg, conn)
    _questions_json_path(cfg).write_text(json.dumps([
        {"id": "keep0001", "question": "Bleibt", "answer": "ok",
         "error": None, "created_at": "2026-01-02T10:00:00+00:00"},
        {"id": "gone0001", "question": "Weg", "answer": "ok",
         "error": None, "created_at": "2026-01-02T11:00:00+00:00"},
    ]), encoding="utf-8")

    resp = client.post("/transcripts/vid001/questions/gone0001/delete", follow_redirects=True)
    assert resp.status_code == 200

    data = json.loads(_questions_json_path(cfg).read_text(encoding="utf-8"))
    assert [e["id"] for e in data] == ["keep0001"]


def test_delete_question_unknown_id_returns_404(client, cfg, conn):
    _seed_done_video(cfg, conn)
    resp = client.post("/transcripts/vid001/questions/doesnotexist/delete")
    assert resp.status_code == 404


def test_delete_question_404_for_unknown_video(client):
    resp = client.post("/transcripts/does-not-exist/questions/abc12345/delete")
    assert resp.status_code == 404
