"""Startseite: Zustände wie 'ytdigest status', letzte Fehler, globaler
'Run jetzt'-Button (Endpunkt dafür liegt in jobs_views.py)."""

from __future__ import annotations

from flask import Blueprint, render_template

from ytdigest.db.repo import Repo
from ytdigest.db.schema import connect
from ytdigest.web.context import get_cfg, get_jobs

bp = Blueprint("dashboard", __name__)

_STATUSES = ("discovered", "processing", "done", "no_transcript", "failed", "skipped")

# wie transcripts_views._PENDING_STATUSES, hier aber kanalübergreifend (kein
# feed_id-Filter) für die Inbox-Übersicht unten auf dem Dashboard
_PENDING_STATUSES = ("discovered", "processing", "failed", "no_transcript", "skipped")
_INBOX_LIMIT = 20


@bp.get("/")
def index():
    conn = connect(get_cfg().paths.database)
    try:
        repo = Repo(conn)
        counts = repo.counts_by_status()
        errors = repo.recent_errors(10)
        recent_done = repo.recent_done(10)
        pending = repo.list_videos(statuses=_PENDING_STATUSES)
        feeds_by_id = {f.id: f for f in repo.list_feeds()}
    finally:
        conn.close()
    rows = [(name, counts.get(name, 0)) for name in _STATUSES]
    recent = [(video, feeds_by_id.get(video.feed_id)) for video in recent_done]
    inbox = [(video, feeds_by_id.get(video.feed_id)) for video in pending[:_INBOX_LIMIT]]
    return render_template("dashboard.html", rows=rows, errors=errors, recent=recent,
                          inbox=inbox, inbox_total=len(pending), jobs=get_jobs())
