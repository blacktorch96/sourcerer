"""Transkripte browsen, im Browser lesen und herunterladen."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, render_template, request, send_file

from ytdigest.db.repo import Repo
from ytdigest.db.schema import connect
from ytdigest.models import Feed
from ytdigest.web.context import get_cfg

bp = Blueprint("transcripts", __name__, url_prefix="/transcripts")


def _feed_for(repo: Repo, feed_id: int) -> Feed | None:
    for feed in repo.list_feeds():
        if feed.id == feed_id:
            return feed
    return None


def _safe_output_path(rel: str | None) -> Path:
    """Löst einen in der DB gespeicherten relativen Pfad gegen output_dir auf.

    Die Pfade stammen aus der eigenen DB, nicht aus der Anfrage - die
    '..'-Prüfung ist trotzdem billig und macht die Route robust, falls ein
    Datensatz je manipuliert oder fehlerhaft geschrieben würde."""
    if not rel:
        abort(404)
    output_dir = get_cfg().paths.output_dir.resolve()
    candidate = (output_dir / rel).resolve()
    if not candidate.is_relative_to(output_dir) or not candidate.is_file():
        abort(404)
    return candidate


@bp.get("")
def index():
    conn = connect(get_cfg().paths.database)
    try:
        repo = Repo(conn)
        feeds = repo.list_feeds()
        feed_id = request.args.get("feed", type=int)
        videos = repo.list_videos(feed_id=feed_id, statuses=("done",))
    finally:
        conn.close()
    return render_template("transcripts.html", feeds=feeds, videos=videos,
                          selected_feed_id=feed_id)


@bp.get("/<video_id>")
def detail(video_id: str):
    conn = connect(get_cfg().paths.database)
    try:
        repo = Repo(conn)
        video = repo.get_video(video_id)
        if video is None or video.status != "done":
            abort(404)
        feed = _feed_for(repo, video.feed_id)
    finally:
        conn.close()
    text = _safe_output_path(video.transcript_path).read_text(encoding="utf-8")
    return render_template("transcript_detail.html", video=video, feed=feed, text=text)


@bp.get("/<video_id>/download/<ext>")
def download(video_id: str, ext: str):
    if ext not in ("txt", "json"):
        abort(404)
    conn = connect(get_cfg().paths.database)
    try:
        repo = Repo(conn)
        video = repo.get_video(video_id)
        if video is None or video.status != "done":
            abort(404)
    finally:
        conn.close()
    rel = video.transcript_path if ext == "txt" else video.metadata_path
    path = _safe_output_path(rel)
    return send_file(path, as_attachment=True, download_name=path.name)
