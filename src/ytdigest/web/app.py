"""Flask-App-Factory. Registriert die Blueprints, hält Config und JobRunner
im App-Kontext (siehe context.py)."""

from __future__ import annotations

from flask import Flask

from ytdigest.config import Config
from ytdigest.web.jobs import JobRunner


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__)
    # keine Login-/CSRF-relevante Nutzung von Sessions (nur 'flash()' für
    # Formular-Rückmeldungen) - ein fester Key genügt für den rein lokalen Gebrauch
    app.secret_key = "ytdigest-local-web"
    app.config["YTDIGEST_CFG"] = cfg
    app.extensions["ytdigest_jobs"] = JobRunner(cfg)

    from ytdigest.web.dashboard_views import bp as dashboard_bp
    from ytdigest.web.feeds_views import bp as feeds_bp
    from ytdigest.web.jobs_views import bp as jobs_bp
    from ytdigest.web.transcripts_views import bp as transcripts_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(feeds_bp)
    app.register_blueprint(transcripts_bp)
    app.register_blueprint(jobs_bp)
    return app
