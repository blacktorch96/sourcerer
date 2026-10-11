from __future__ import annotations

from pathlib import Path

import pytest

from ytdigest.web import settings_views
from ytdigest.web.app import create_app


@pytest.fixture
def app(cfg):
    # eigener config.toml-Pfad in tmp_path - sonst fiele settings_views auf
    # './config.toml' zurück und würde beim Schreiben die echte Projektdatei
    # im Arbeitsverzeichnis treffen
    cfg.source_path = cfg.paths.database.parent / "config.toml"
    flask_app = create_app(cfg)
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def no_real_ollama(monkeypatch):
    # jeder GET auf /settings fragt list_models() ab (auch nach einem POST
    # mit follow_redirects=True) - ohne diesen Default würde das einen
    # echten Netzwerkaufruf an Ollama auslösen
    monkeypatch.setattr(settings_views.ollama_summarize, "list_models", lambda cfg: [])


def test_settings_page_shows_current_values(client, cfg):
    cfg.filters.min_duration_min = 12
    cfg.summary.model = "qwen3.5:9b"
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b'value="12"' in resp.data


def test_update_filters_saves_and_applies_immediately(client, cfg):
    resp = client.post("/settings/filters", data={"min_duration_min": "20"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert cfg.filters.min_duration_min == 20
    assert "min_duration_min = 20" in Path(cfg.source_path).read_text(encoding="utf-8")


def test_update_filters_rejects_negative_value(client, cfg):
    cfg.filters.min_duration_min = 8
    resp = client.post("/settings/filters", data={"min_duration_min": "-1"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert cfg.filters.min_duration_min == 8
    assert not cfg.source_path.exists()


def test_update_filters_rejects_non_numeric_value(client, cfg):
    cfg.filters.min_duration_min = 8
    resp = client.post("/settings/filters", data={"min_duration_min": "abc"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert cfg.filters.min_duration_min == 8


def test_update_summary_model_saves_and_applies_immediately(client, cfg):
    resp = client.post("/settings/summary-model", data={"model": "gpt-oss:20b"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert cfg.summary.model == "gpt-oss:20b"
    assert 'model = "gpt-oss:20b"' in Path(cfg.source_path).read_text(encoding="utf-8")


def test_update_summary_model_rejects_empty(client, cfg):
    cfg.summary.model = "qwen3.5:9b"
    resp = client.post("/settings/summary-model", data={"model": "  "},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert cfg.summary.model == "qwen3.5:9b"


def test_settings_page_shows_dropdown_when_ollama_reachable(client, cfg, monkeypatch):
    monkeypatch.setattr(settings_views.ollama_summarize, "list_models",
                        lambda cfg: ["qwen3.5:9b", "gpt-oss:20b"])
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b"<select" in resp.data
    assert b"gpt-oss:20b" in resp.data


def test_settings_page_shows_textbox_when_ollama_unreachable(client, cfg):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b"<select" not in resp.data
    assert b"nicht erreichbar" in resp.data
