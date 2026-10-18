from __future__ import annotations

import pytest

from ytdigest.web import profiles_views
from ytdigest.web.app import create_app


@pytest.fixture
def app(cfg, conn):
    flask_app = create_app(cfg)
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_profile(tmp_path, name, db_filename="other.sqlite3"):
    profile_dir = tmp_path / "profiles" / name
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.toml").write_text(
        f'[paths]\ndatabase = "{(tmp_path / db_filename).as_posix()}"\n',
        encoding="utf-8",
    )
    return profile_dir / "config.toml"


def test_switch_updates_cfg_and_jobrunner(app, client, cfg, tmp_path, monkeypatch):
    _make_profile(tmp_path, "studium")
    monkeypatch.setattr(profiles_views, "list_profiles", lambda: ["studium"])
    monkeypatch.setattr(profiles_views, "profile_config_path",
                        lambda name: tmp_path / "profiles" / name / "config.toml")

    # kein follow_redirects: die neue DB (anderes Profil) hat hier absichtlich
    # kein Schema - es geht nur um den Config-/JobRunner-Zustand nach dem Switch
    resp = client.post("/profile/switch", data={"profile": "studium"})
    assert resp.status_code == 302

    new_cfg = app.config["YTDIGEST_CFG"]
    assert new_cfg is not cfg
    assert new_cfg.paths.database == tmp_path / "other.sqlite3"
    assert app.extensions["ytdigest_jobs"].cfg is new_cfg


def test_switch_rejects_unknown_profile(app, client, monkeypatch):
    monkeypatch.setattr(profiles_views, "list_profiles", lambda: ["studium"])
    resp = client.post("/profile/switch", data={"profile": "does-not-exist"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert app.config["YTDIGEST_CFG"].source_path is None  # unverändert


def test_switch_rejects_while_job_running(app, client, cfg, monkeypatch):
    monkeypatch.setattr(profiles_views, "list_profiles", lambda: ["studium"])
    app.extensions["ytdigest_jobs"]._thread = type("Fake", (), {"is_alive": lambda self: True})()

    resp = client.post("/profile/switch", data={"profile": "studium"}, follow_redirects=True)
    assert resp.status_code == 200
    assert app.config["YTDIGEST_CFG"] is cfg


def test_dashboard_shows_profile_dropdown_when_profiles_exist(client, monkeypatch):
    monkeypatch.setattr(profiles_views, "list_profiles", lambda: ["studium", "technologie"])
    # Dropdown wird über den context_processor in app.py befüllt, der direkt
    # ytdigest.profiles importiert - für diesen Smoke-Test patchen wir dort
    import ytdigest.web.app as app_module
    monkeypatch.setattr(app_module, "list_profiles", lambda: ["studium", "technologie"])

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"studium" in resp.data
    assert b"technologie" in resp.data


def test_dashboard_hides_profile_dropdown_without_profiles(client, monkeypatch):
    import ytdigest.web.app as app_module
    monkeypatch.setattr(app_module, "list_profiles", lambda: [])

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"profile-switch" not in resp.data
