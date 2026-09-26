"""Feed-Verwaltung für die Web-Oberfläche. feeds.txt bleibt Quelle der
Wahrheit (wie beim CLI-Befehl 'feeds add') - die UI liest/schreibt sie nur."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from ytdigest.db.repo import Repo
from ytdigest.db.schema import connect
from ytdigest.feeds.parser import append_line, parse_line, remove_line
from ytdigest.models import Feed
from ytdigest.pipeline import RunOptions
from ytdigest.web.context import get_cfg, get_jobs

bp = Blueprint("feeds", __name__, url_prefix="/feeds")


def _repo() -> tuple[Repo, sqlite3.Connection]:
    conn = connect(get_cfg().paths.database)
    return Repo(conn), conn


def _get_feed_or_404(repo: Repo, feed_id: int) -> Feed:
    for feed in repo.list_feeds():
        if feed.id == feed_id:
            return feed
    abort(404)


@bp.get("")
def index():
    repo, conn = _repo()
    try:
        feeds = repo.list_feeds()
        counts = repo.video_counts_per_feed()
    finally:
        conn.close()
    return render_template("feeds.html", feeds=feeds, counts=counts, jobs=get_jobs())


@bp.post("/add")
def add():
    source = request.form.get("source", "").strip()
    name = request.form.get("name", "").strip() or None
    if not source:
        flash("Bitte eine Quelle angeben.", "error")
        return redirect(url_for("feeds.index"))

    line = f"{source} | {name}" if name else source
    try:
        parse_line(line)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("feeds.index"))

    feeds_file = get_cfg().paths.feeds_file
    existing = feeds_file.read_text(encoding="utf-8") if feeds_file.exists() else ""
    if line in existing.splitlines():
        flash("Zeile ist schon in feeds.txt vorhanden.", "error")
        return redirect(url_for("feeds.index"))
    append_line(feeds_file, line)

    jobs = get_jobs()
    started = jobs.start("Sync (neuer Feed)", lambda p: p.run(RunOptions(sync_only=True)))
    if started:
        flash(f"Hinzugefügt: {line} - Sync läuft.", "ok")
    else:
        flash(f"Hinzugefügt: {line} - Sync folgt beim nächsten Lauf "
              f"(gerade läuft schon: {jobs.label}).", "ok")
    return redirect(url_for("feeds.index"))


@bp.post("/<int:feed_id>/remove")
def remove(feed_id: int):
    repo, conn = _repo()
    try:
        feed = _get_feed_or_404(repo, feed_id)
        removed = remove_line(get_cfg().paths.feeds_file, feed)
    finally:
        conn.close()
    if removed:
        flash(f"{feed.name} aus feeds.txt entfernt (wird beim nächsten Sync deaktiviert).", "ok")
    else:
        flash(f"Zeile für {feed.name} nicht in feeds.txt gefunden.", "error")
    return redirect(url_for("feeds.index"))


@bp.post("/<int:feed_id>/sync")
def sync_one(feed_id: int):
    """Wie jobs.run_all: JSON statt Redirect, weil das Formular per Fetch
    angesteuert wird (app.js hängt daran den Live-Log-Stream)."""
    repo, conn = _repo()
    try:
        feed = _get_feed_or_404(repo, feed_id)
    finally:
        conn.close()
    jobs = get_jobs()
    started = jobs.start(
        f"Sync: {feed.name}",
        lambda p: p.run(RunOptions(feed_channel_ids=[feed.channel_id])),
    )
    if not started:
        return jsonify(ok=False, error=f"Läuft schon: {jobs.label}"), 409
    return jsonify(ok=True)


@bp.post("/<int:feed_id>/backfill")
def backfill(feed_id: int):
    repo, conn = _repo()
    try:
        feed = _get_feed_or_404(repo, feed_id)
    finally:
        conn.close()
    days = request.form.get("days", type=int) or 365
    limit = request.form.get("limit", type=int) or None
    jobs = get_jobs()
    started = jobs.start(
        f"Backfill: {feed.name} ({days}d)",
        lambda p: p.backfill_channel(feed.channel_id, days=days, limit=limit),
    )
    if not started:
        return jsonify(ok=False, error=f"Läuft schon: {jobs.label}"), 409
    return jsonify(ok=True)


@bp.post("/<int:feed_id>/retry")
def retry(feed_id: int):
    repo, conn = _repo()
    try:
        feed = _get_feed_or_404(repo, feed_id)
        n = repo.requeue_failed(include_no_transcript=True, feed_ids=[feed.id])
        n += repo.requeue_skipped(feed_ids=[feed.id])
    finally:
        conn.close()
    flash(f"{n} Video(s) zurückgesetzt - nächster Sync/Run holt sie nach.", "ok")
    return redirect(url_for("feeds.index"))
