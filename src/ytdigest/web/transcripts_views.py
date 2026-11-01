"""Transkripte browsen, im Browser lesen und herunterladen."""

from __future__ import annotations

import re
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from markupsafe import Markup, escape

from ytdigest.db.repo import Repo
from ytdigest.db.schema import connect
from ytdigest.feeds.fetcher import fetch_feed
from ytdigest.feeds.podcast_fetcher import fetch_podcast_feed
from ytdigest.models import Feed
from ytdigest.pipeline import RunOptions
from ytdigest.sources import local, podcast
from ytdigest.web.context import get_cfg, get_jobs
from ytdigest.web.summary_views import (  # experimentell, siehe dortige Moduldoc
    get_questions,
    get_summary_state,
)

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


def _resolve_output_path(rel: str | None) -> Path | None:
    """Löst einen in der DB gespeicherten relativen Pfad gegen output_dir auf,
    oder None statt 404 - für Stellen wie die Volltextsuche, die eine
    einzelne nicht auflösbare Datei überspringen statt die ganze Anfrage
    abzubrechen.

    Die Pfade stammen aus der eigenen DB, nicht aus der Anfrage - die
    '..'-Prüfung ist trotzdem billig und macht die Route robust, falls ein
    Datensatz je manipuliert oder fehlerhaft geschrieben würde."""
    if not rel:
        return None
    output_dir = get_cfg().paths.output_dir.resolve()
    candidate = (output_dir / rel).resolve()
    if not candidate.is_relative_to(output_dir) or not candidate.is_file():
        return None
    return candidate


def _safe_output_path(rel: str | None) -> Path:
    candidate = _resolve_output_path(rel)
    if candidate is None:
        abort(404)
    return candidate


_SEARCH_MAX_RESULTS = 50
_SNIPPET_CONTEXT = 80  # Zeichen vor/nach dem Fund im Vorschau-Ausschnitt


def _snippet(text: str, needle: str) -> Markup:
    """Kurzer, escaped Ausschnitt um den ersten Fund von ``needle`` in
    ``text``, mit <mark> um das Suchwort - für die Trefferliste der
    Volltextsuche. Groß-/Kleinschreibung des Fundorts bleibt erhalten."""
    lower = text.lower()
    idx = lower.find(needle.lower())
    if idx == -1:
        return Markup(escape(text[: _SNIPPET_CONTEXT * 2]))
    start = max(0, idx - _SNIPPET_CONTEXT)
    end = min(len(text), idx + len(needle) + _SNIPPET_CONTEXT)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    before = text[start:idx]
    match = text[idx : idx + len(needle)]
    after = text[idx + len(needle) : end]
    return Markup(f"{prefix}{escape(before)}<mark>{escape(match)}</mark>{escape(after)}{suffix}")


def _search_transcripts(repo: Repo, *, feed_id: int | None, query: str) -> list[tuple]:
    """Lineare Volltextsuche über die Transkript-Dateien aller fertigen
    Videos (optional auf einen Feed eingeschränkt) - für die Größenordnung
    eines persönlichen Archivs reicht das, ohne einen eigenen Such-Index
    pflegen zu müssen. Bricht bei ``_SEARCH_MAX_RESULTS`` Treffern ab."""
    feeds_by_id = {feed.id: feed for feed in repo.list_feeds()}
    needle = query.lower()
    results: list[tuple] = []
    for video in repo.list_videos(feed_id=feed_id, statuses=("done",)):
        path = _resolve_output_path(video.transcript_path)
        if path is None:
            continue
        text = path.read_text(encoding="utf-8")
        if needle not in text.lower() and needle not in video.title.lower():
            continue
        snippet_source = text if needle in text.lower() else video.title
        results.append((video, feeds_by_id.get(video.feed_id), _snippet(snippet_source, query)))
        if len(results) >= _SEARCH_MAX_RESULTS:
            break
    return results


@bp.get("")
def index():
    query = request.args.get("q", "").strip()
    conn = connect(get_cfg().paths.database)
    try:
        repo = Repo(conn)
        feeds = repo.list_feeds()
        feed_id = request.args.get("feed", type=int)
        sync_error = None
        pending = []
        search_results = []
        if query:
            # Volltextsuche ersetzt die normale Liste für diese Anfrage -
            # kein Live-Sync nötig, es wird ja nur im bereits Vorhandenen gesucht.
            search_results = _search_transcripts(repo, feed_id=feed_id, query=query)
            videos = []
        else:
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
    # has_summary nur für fertige Videos prüfen (and kurzschließt vorher) -
    # experimentell, siehe web/summary_views.py
    rows = [(video, is_done, is_done and get_summary_state(video.transcript_path).text is not None)
           for video, is_done in rows]
    return render_template("transcripts.html", feeds=feeds, rows=rows,
                          selected_feed_id=feed_id, sync_error=sync_error, jobs=get_jobs(),
                          query=query, search_results=search_results)


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
    summary = get_summary_state(video.transcript_path)
    questions = get_questions(video.transcript_path)
    return render_template("transcript_detail.html", video=video, feed=feed,
                          paragraphs=paragraphs, summary=summary, questions=questions,
                          jobs=get_jobs())


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
    """Ein einzelnes Video gezielt herunterladen und transkribieren (Button
    bei den noch offenen Videos in der Status-Spalte). min_duration_min=0
    hebt den Dauerfilter für diesen einen, bewusst angeklickten Fall auf -
    so lässt sich auch ein zuvor als 'zu kurz' übersprungenes Video auf
    Wunsch trotzdem transkribieren."""
    conn = connect(get_cfg().paths.database)
    try:
        video = Repo(conn).get_video(video_id)
    finally:
        conn.close()
    if video is None:
        abort(404)

    jobs = get_jobs()
    started = jobs.start(f"Verarbeite: {video.title}",
                         lambda p: p.process_one(video_id, RunOptions(min_duration_min=0)))
    if not started:
        return jsonify(ok=False, error=f"Läuft schon: {jobs.label}"), 409
    return jsonify(ok=True)


@bp.post("/<path:video_id>/dismiss")
def dismiss(video_id: str):
    """Inbox: Video ohne Herunterladen/Transkribieren als erledigt markieren
    (z. B. kein Interesse am Inhalt). Landet als skipped/dismissed - taucht
    in der Inbox nicht mehr auf, bleibt aber über 'Retry' je Feed erreichbar,
    falls man es sich anders überlegt (requeue_skipped() läuft nur über den
    pipeline-eigenen Grund 'initial_sync', lässt 'dismissed' also in Ruhe)."""
    conn = connect(get_cfg().paths.database)
    try:
        repo = Repo(conn)
        video = repo.get_video(video_id)
        if video is None:
            abort(404)
        repo.skip_video(video_id, "dismissed")
    finally:
        conn.close()
    flash(f'"{video.title}" als gelesen markiert.', "ok")
    return redirect(request.referrer or url_for("dashboard.index"))
