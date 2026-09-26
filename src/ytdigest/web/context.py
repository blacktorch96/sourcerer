"""Config/JobRunner aus dem Flask-App-Kontext holen - eigenes Modul, damit
app.py und die Blueprints sich nicht gegenseitig importieren müssen."""

from __future__ import annotations

from flask import current_app

from ytdigest.config import Config
from ytdigest.web.jobs import JobRunner


def get_cfg() -> Config:
    return current_app.config["YTDIGEST_CFG"]


def get_jobs() -> JobRunner:
    return current_app.extensions["ytdigest_jobs"]
