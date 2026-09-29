"""Flask-App-Factory. Registriert die Blueprints, hält Config und JobRunner
im App-Kontext (siehe context.py)."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from ytdigest.config import Config
from ytdigest.web.jobs import JobRunner


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__)
    # keine Login-/CSRF-relevante Nutzung von Sessions (nur 'flash()' für
    # Formular-Rückmeldungen) - ein fester Key genügt für den rein lokalen Gebrauch
    app.secret_key = "ytdigest-local-web"
    # sonst nur im Debugmodus an: Templates würden nach dem ersten Rendern
    # sonst pro Prozess gecacht, eine Änderung an z. B. feeds.html bräuchte
    # dann einen Serverneustart statt nur ein Neuladen der Seite
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["YTDIGEST_CFG"] = cfg
    app.extensions["ytdigest_jobs"] = JobRunner(cfg)

    static_dir = Path(app.static_folder)

    def asset_url(filename: str) -> str:
        """URL für eine statische Datei mit Cache-Buster (Datei-mtime als
        Query-Param), damit ein CSS/JS-Update nicht durch den Browser-Cache
        verdeckt wird - ohne diesen Parameter reicht ein normales Neuladen
        (kein Hard-Refresh) nach einer Änderung sonst oft nicht aus."""
        from flask import url_for

        path = static_dir / filename
        version = int(path.stat().st_mtime) if path.is_file() else 0
        return f"{url_for('static', filename=filename)}?v={version}"

    app.jinja_env.globals["asset_url"] = asset_url

    from ytdigest.web.dashboard_views import bp as dashboard_bp
    from ytdigest.web.feeds_views import bp as feeds_bp
    from ytdigest.web.jobs_views import bp as jobs_bp
    from ytdigest.web.transcripts_views import bp as transcripts_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(feeds_bp)
    app.register_blueprint(transcripts_bp)
    app.register_blueprint(jobs_bp)
    return app
