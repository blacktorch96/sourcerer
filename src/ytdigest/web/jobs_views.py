"""Seitenübergreifende Job-Endpunkte: globaler 'Sync & Verarbeitung'-Lauf,
Live-Log-Stream (SSE) und Status-Poll. Pro-Feed-Aktionen (Sync/Backfill/Retry
für einen einzelnen Kanal) liegen in feeds_views.py."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify

from ytdigest.pipeline import RunOptions
from ytdigest.web.context import get_jobs

bp = Blueprint("jobs", __name__, url_prefix="/jobs")


@bp.post("/run")
def run_all():
    jobs = get_jobs()
    started = jobs.start("Sync & Verarbeitung", lambda pipeline: pipeline.run(RunOptions()))
    if not started:
        return jsonify(ok=False, error=f"Läuft schon: {jobs.label}"), 409
    return jsonify(ok=True)


@bp.get("/status")
def status():
    jobs = get_jobs()
    return jsonify(running=jobs.running, label=jobs.label)


@bp.get("/stream")
def stream():
    jobs = get_jobs()
    if not jobs.running:
        return Response('event: done\ndata: {"type": "done", "idle": true}\n\n',
                        mimetype="text/event-stream")
    q = jobs.subscribe()
    return Response(jobs.stream(q), mimetype="text/event-stream")
