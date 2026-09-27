"""Transkripte browsen, im Browser lesen und herunterladen."""

from __future__ import annotations

import re
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, send_file

from ytdigest.db.repo import Repo
from ytdigest.db.schema import connect
from ytdigest.feeds.fetcher import fetch_feed
from ytdigest.feeds.podcast_fetcher import fetch_podcast_feed
from ytdigest.models import Feed
from ytdigest.sources import local, podcast
from ytdigest.web.context import get_cfg, get_jobs

bp = Blueprint("transcripts", __name__, url_prefix="/transcripts")

_PENDING_STATUSES = ("discovered", "processing", "failed", "no_transcript", "skipped")
# 'skipped' mit skip_reason='too_short' bleibt mit drin, statt ganz zu
# verschwinden: die Dauer steht beim RSS-Sync noch nicht fest (kommt erst mit
# dem yt-dlp-Metadatenabruf während der Verarbeitung), sie tauchen also erst
# als 'noch nicht verfügbar' auf. Würde so ein Video nach einem Klick auf
# "herunterladen" einfach aus der Liste verschwinden, sähe das wie ein
# stiller Fehler aus - das Template zeigt stattdessen "übersprungen (zu
# kurz)" ohne Aktions-Button (siehe transcripts.html).


def _feed_for(repo: Repo, feed_id: int) -> Feed | None:
    for feed in repo.list_feeds():
        if feed.id == feed_id:
            return feed
    return None


def _sync_feed_quick(repo: Repo, feed: Feed) -> str | None:
    """Kurzer Live-Abgleich für die Detailansicht eines Feeds: neue Einträge
    seit dem letzten Sync als 'discovered' anlegen, damit sie hier als 'noch
    nicht verfügbar' auftauchen, auch wenn der nächste reguläre Sync/Cron-Lauf
    noch nicht dran war. Nutzt Conditional GET (billig bei unverändertem Feed).
    Gibt bei einem Fehler eine kurze Meldung zurück - die Seite bleibt mit den
    schon bekannten Videos trotzdem nutzbar, statt an einem Netzwerkfehler
    zu scheitern."""
    if local.is_local_feed(feed.feed_url):
        return None  # lokale Kanäle haben kein RSS - siehe 'local scan'

    cfg = get_cfg()
    timeout = cfg.feeds.request_timeout_s
    try:
        if podcast.is_podcast_feed(feed.channel_id):
            fetch = fetch_podcast_feed(feed.feed_url, channel_id=feed.channel_id,
                                       timeout_s=timeout, etag=feed.etag,
                                       last_modified=feed.last_modified, conditional=True)
        else:
            fetch = fetch_feed(feed.feed_url, timeout_s=timeout, etag=feed.etag,
                               last_modified=feed.last_modified, conditional=True)
    except Exception as exc:  # noqa: BLE001 - Vorschau darf nie an einem Fehler scheitern
        return str(exc)

    if fetch.error:
        return fetch.error
    if fetch.not_modified:
        repo.mark_feed_checked(feed.id, etag=fetch.etag, last_modified=fetch.last_modified)
        return None

    for entry in fetch.entries:
        if not repo.video_exists(entry.video_id):
            repo.add_video(entry, feed.id, status="discovered", duration_s=entry.duration_s)
    repo.mark_feed_checked(feed.id, etag=fetch.etag, last_modified=fetch.last_modified)
    return None


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SOFT_PARAGRAPH_CHARS = 500  # Zielgröße für die Notlösung unten


def _split_long_block(paragraph: str) -> list[str]:
    """Notlösung nur für die Vorschau: manche Quellen (v. a. captions_auto -
    YouTubes Auto-Untertitel haben oft lückenlose Cue-Zeiten ohne erkennbare
    Sprechpause) liefern gar keine '\\n\\n'-Absatzgrenzen, sodass eine ganze
    Folge als ein einziger, mehrere tausend Zeichen langer Block ankommt.
    Der wird hier zusätzlich an Satzgrenzen in lesbare Häppchen aufgeteilt."""
    sentences = [s for s in _SENTENCE_SPLIT.split(paragraph) if s]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for sentence in sentences:
        current.append(sentence)
        length += len(sentence)
        if length >= _SOFT_PARAGRAPH_CHARS:
            chunks.append(" ".join(current))
            current = []
            length = 0
    if current:
        chunks.append(" ".join(current))
    return chunks or [paragraph]


def _dewrap_paragraphs(text: str) -> list[str]:
    """Für die Browser-Vorschau: die feste Zeilenbreite aus reflow.wrap_transcript
    (config.toml: output.line_width) wieder aufheben, indem die Zeilen je
    Absatz zu einem Fließtext verbunden werden - der Browser umbricht dann
    selbst nach Fensterbreite. Echte Absatzgrenzen (Leerzeilen, z. B. aus der
    Sprechpausen- oder Kapitelerkennung) bleiben erhalten; fehlen sie ganz
    (siehe ``_split_long_block``), wird zusätzlich an Satzgrenzen aufgeteilt.
    Die gespeicherte .txt-Datei bleibt davon unberührt (Download liefert sie
    unverändert)."""
    raw_paragraphs = [p for p in text.strip("\n").split("\n\n") if p.strip()]
    joined = [" ".join(line.strip() for line in p.splitlines() if line.strip())
              for p in raw_paragraphs]
    result: list[str] = []
    for paragraph in joined:
        if len(paragraph) > _SOFT_PARAGRAPH_CHARS * 1.5:
            result.extend(_split_long_block(paragraph))
        else:
            result.append(paragraph)
    return result


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
        sync_error = None
        pending = []
        if feed_id is not None:
            feed = _feed_for(repo, feed_id)
            if feed is not None:
                sync_error = _sync_feed_quick(repo, feed)
                pending = repo.list_videos(feed_id=feed_id, statuses=_PENDING_STATUSES)
        videos = repo.list_videos(feed_id=feed_id, statuses=("done",))
    finally:
        conn.close()
    rows = sorted(
        [(v, True) for v in videos] + [(v, False) for v in pending],
        key=lambda pair: pair[0].published_at, reverse=True,
    )
    return render_template("transcripts.html", feeds=feeds, rows=rows,
                          selected_feed_id=feed_id, sync_error=sync_error, jobs=get_jobs())


@bp.get("/<path:video_id>")
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
    paragraphs = _dewrap_paragraphs(text)
    return render_template("transcript_detail.html", video=video, feed=feed,
                          paragraphs=paragraphs)


@bp.get("/<path:video_id>/download/<ext>")
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


@bp.post("/<path:video_id>/process")
def process(video_id: str):
    """Ein einzelnes, noch nicht fertiges Video gezielt herunterladen und
    transkribieren (Button bei den 'noch nicht verfügbaren' Videos)."""
    conn = connect(get_cfg().paths.database)
    try:
        video = Repo(conn).get_video(video_id)
    finally:
        conn.close()
    if video is None:
        abort(404)

    jobs = get_jobs()
    started = jobs.start(f"Verarbeite: {video.title}",
                         lambda p: p.process_one(video_id))
    if not started:
        return jsonify(ok=False, error=f"Läuft schon: {jobs.label}"), 409
    return jsonify(ok=True)
