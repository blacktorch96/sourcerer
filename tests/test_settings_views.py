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


def test_update_summary_max_chars_saves_and_applies_immediately(client, cfg):
    resp = client.post("/settings/summary-max-chars", data={"max_chars": "1500"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert cfg.summary.max_chars == 1500
    assert "max_chars = 1500" in Path(cfg.source_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["", "abc", "50", "-5"])
def test_update_summary_max_chars_rejects_invalid(client, cfg, value):
    before = cfg.summary.max_chars
    resp = client.post("/settings/summary-max-chars", data={"max_chars": value},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert cfg.summary.max_chars == before
    assert not cfg.source_path.exists()


def test_settings_page_shows_current_prompt(client, cfg):
    cfg.summary.prompt_template = "Fasse zusammen: {text}"
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b"Fasse zusammen: {text}" in resp.data
    # Hinweistext zeigt die Platzhalter wörtlich (Jinja-Escapes im Template)
    assert b"<code>{text}</code>" in resp.data
    assert b"<code>{{</code>" in resp.data


def test_update_summary_prompt_saves_and_applies_immediately(client, cfg):
    prompt = "Kurz auf Deutsch (max {max_chars} Zeichen):\r\n\r\n- Stichpunkte\r\n\r\n{text}"
    resp = client.post("/settings/summary-prompt", data={"prompt": prompt},
                       follow_redirects=True)
    assert resp.status_code == 200
    expected = "Kurz auf Deutsch (max {max_chars} Zeichen):\n\n- Stichpunkte\n\n{text}"
    assert cfg.summary.prompt_template == expected

    # mehrzeilig lesbar in der Datei und beim erneuten Laden identisch
    import tomllib
    text = Path(cfg.source_path).read_text(encoding="utf-8")
    assert '"""' in text
    assert tomllib.loads(text)["summary"]["prompt_template"] == expected


def test_saved_prompt_is_used_for_summaries(client, cfg):
    from ytdigest.config import Config

    client.post("/settings/summary-prompt", data={"prompt": "Mach daraus {max_chars}: {text}"})
    import tomllib
    data = tomllib.loads(Path(cfg.source_path).read_text(encoding="utf-8"))
    reloaded = Config.model_validate(data)
    assert reloaded.summary.prompt_template.format(text="T", max_chars=5) == "Mach daraus 5: T"


def test_update_summary_prompt_requires_text_placeholder(client, cfg):
    before = cfg.summary.prompt_template
    resp = client.post("/settings/summary-prompt", data={"prompt": "Ohne Platzhalter"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"Platzhalter" in resp.data
    assert cfg.summary.prompt_template == before
    assert not cfg.source_path.exists()


@pytest.mark.parametrize("prompt", [
    "{text} und {unbekannt}",
    "{text} mit { offener Klammer",
    "{text} und {0}",
])
def test_update_summary_prompt_rejects_broken_braces(client, cfg, prompt):
    before = cfg.summary.prompt_template
    resp = client.post("/settings/summary-prompt", data={"prompt": prompt},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"geschweifte Klammer" in resp.data
    assert cfg.summary.prompt_template == before


def test_update_summary_prompt_allows_escaped_literal_braces(client, cfg):
    client.post("/settings/summary-prompt", data={"prompt": 'Antwort als {{"a": 1}}: {text}'})
    assert cfg.summary.prompt_template == 'Antwort als {{"a": 1}}: {text}'


def test_reset_summary_prompt_restores_default_and_drops_key(client, cfg):
    from ytdigest.config import SummaryCfg

    client.post("/settings/summary-prompt", data={"prompt": "Eigener Prompt {text}"})
    assert "prompt_template" in Path(cfg.source_path).read_text(encoding="utf-8")

    resp = client.post("/settings/summary-prompt/reset", follow_redirects=True)
    assert resp.status_code == 200
    assert cfg.summary.prompt_template == SummaryCfg().prompt_template
    assert "prompt_template" not in Path(cfg.source_path).read_text(encoding="utf-8")


def test_reset_button_only_shown_for_customized_prompt(client, cfg):
    assert b"Standard wiederherstellen" not in client.get("/settings").data
    cfg.summary.prompt_template = "Eigener Prompt {text}"
    assert b"Standard wiederherstellen" in client.get("/settings").data


def test_settings_page_shows_dropdown_when_ollama_reachable(client, cfg, monkeypatch):
    monkeypatch.setattr(settings_views.ollama_summarize, "list_models",
                        lambda cfg: ["qwen3.5:9b", "gpt-oss:20b"])
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b'<select id="model"' in resp.data
    assert b"gpt-oss:20b" in resp.data


def test_settings_page_shows_textbox_when_ollama_unreachable(client, cfg):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b'<select id="model"' not in resp.data
    assert b"nicht erreichbar" in resp.data
