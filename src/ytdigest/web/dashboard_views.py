"""Startseite: Zustände wie 'ytdigest status', letzte Fehler, globaler
'Run jetzt'-Button (Endpunkt dafür liegt in jobs_views.py)."""

from __future__ import annotations

from flask import Blueprint, render_template

from ytdigest.db.repo import Repo
from ytdigest.db.schema import connect
from ytdigest.web.context import get_cfg, get_jobs

bp = Blueprint("dashboard", __name__)

_STATUSES = ("discovered", "processing", "done", "no_transcript", "failed", "skipped")


@bp.get("/")
def index():
    conn = connect(get_cfg().paths.database)
    try:
        repo = Repo(conn)
        counts = repo.counts_by_status()
        errors = repo.recent_errors(10)
    finally:
        conn.close()
    rows = [(name, counts.get(name, 0)) for name in _STATUSES]
    return render_template("dashboard.html", rows=rows, errors=errors, jobs=get_jobs())
