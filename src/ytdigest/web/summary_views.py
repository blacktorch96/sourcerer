"""Web-Endpunkte für die (experimentelle) lokale Zusammenfassung über Ollama.

Bewusst als eigene Datei/eigener Blueprint von den übrigen Transkript-Routen
abgegrenzt: das Feature wird gerade getestet und könnte komplett verworfen
oder grundlegend geändert werden. Zum Entfernen genügt:
  - diese Datei löschen
  - ihre Registrierung in app.py streichen
  - sources/ollama_summarize.py löschen
  - den 'summary'-Import + die Fallunterscheidung in transcripts_views.py
    (detail()) und transcript_detail.html auf den ursprünglichen
    'Zusammenfassung erstellen'-Button zurücksetzen
  - den [summary]-Abschnitt in config.py entfernen
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from flask import Blueprint, abort, jsonify, request

from ytdigest.config import Config
from ytdigest.db.repo import Repo
from ytdigest.db.schema import connect
from ytdigest.models import RunReport, Video
from ytdigest.sources import ollama_summarize
from ytdigest.web.context import get_cfg, get_jobs

log = logging.getLogger(__name__)

bp = Blueprint("summary", __name__, url_prefix="/transcripts")


@dataclass(slots=True)
class SummaryState:
    text: str | None = None
    error: str | None = None


def _paths(transcript_rel: str) -> tuple[Path, Path]:
    base = Path(transcript_rel)
    return base.with_suffix(".summary.md"), base.with_suffix(".summary.error.txt")


def _resolve_existing(output_dir: Path, rel: Path) -> Path | None:
    candidate = (output_dir / rel).resolve()
    if not candidate.is_relative_to(output_dir) or not candidate.is_file():
        return None
    return candidate


def get_summary_state(transcript_path: str | None) -> SummaryState:
    """Für die Detailseite: liest eine vorhandene Zusammenfassung oder den
    Fehler des letzten Versuchs, falls vorhanden. Ohne Netzwerkzugriff -
    liest nur, was schon auf der Platte liegt."""
    if not transcript_path:
        return SummaryState()
    output_dir = get_cfg().paths.output_dir.resolve()
    summary_rel, error_rel = _paths(transcript_path)

    summary_abs = _resolve_existing(output_dir, summary_rel)
    if summary_abs is not None:
        return SummaryState(text=summary_abs.read_text(encoding="utf-8"))

    error_abs = _resolve_existing(output_dir, error_rel)
    if error_abs is not None:
        return SummaryState(error=error_abs.read_text(encoding="utf-8"))

    return SummaryState()


def _done_video_with_transcript(cfg: Config, video_id: str) -> tuple[Video, Path]:
    """Lädt ein fertiges Video + den aufgelösten Pfad zu seinem Transkript,
    oder bricht mit 404 ab (unbekanntes Video, nicht fertig, Datei fehlt)."""
    conn = connect(cfg.paths.database)
    try:
        video = Repo(conn).get_video(video_id)
    finally:
        conn.close()
    if video is None or video.status != "done" or not video.transcript_path:
        abort(404)

    output_dir = cfg.paths.output_dir.resolve()
    transcript_abs = _resolve_existing(output_dir, Path(video.transcript_path))
    if transcript_abs is None:
        abort(404)
    return video, transcript_abs


@bp.post("/<path:video_id>/summarize")
def summarize(video_id: str):
    cfg = get_cfg()
    video, transcript_abs = _done_video_with_transcript(cfg, video_id)
    output_dir = cfg.paths.output_dir.resolve()
    summary_rel, error_rel = _paths(video.transcript_path)
    summary_abs = output_dir / summary_rel
    error_abs = output_dir / error_rel

    def job(_pipeline):
        # Signatur von JobRunner.start() vorgegeben (erwartet einen Callable,
        # der eine Pipeline entgegennimmt) - hier ungenutzt, das Feature hat
        # nichts mit der Sync/ASR-Pipeline zu tun, nutzt JobRunner nur für
        # Single-Job-Lock + Live-Log mit.
        text = transcript_abs.read_text(encoding="utf-8")
        result = ollama_summarize.summarize(text, cfg=cfg.summary)
        if result.text:
            summary_abs.parent.mkdir(parents=True, exist_ok=True)
            summary_abs.write_text(result.text.strip() + "\n", encoding="utf-8")
            error_abs.unlink(missing_ok=True)
        else:
            error_abs.parent.mkdir(parents=True, exist_ok=True)
            error_abs.write_text(result.error or "unbekannter Fehler", encoding="utf-8")
        return RunReport()

    jobs = get_jobs()
    started = jobs.start(f"Zusammenfassung: {video.title}", job)
    if not started:
        return jsonify(ok=False, error=f"Läuft schon: {jobs.label}"), 409
    return jsonify(ok=True)


@bp.post("/<path:video_id>/ask")
def ask(video_id: str):
    """Freie Frage zum Transkript - Antwort wird nicht abgelegt, sondern nur
    live übers Job-Log angezeigt (keine Historie, jede Frage ist für sich
    genommen, immer neu auf Basis des ganzen Transkripts beantwortet)."""
    cfg = get_cfg()
    question = (request.form.get("question") or "").strip()
    if not question:
        return jsonify(ok=False, error="Bitte eine Frage eingeben."), 400

    video, transcript_abs = _done_video_with_transcript(cfg, video_id)

    def job(_pipeline):
        text = transcript_abs.read_text(encoding="utf-8")
        log.info("Frage: %s", question)
        result = ollama_summarize.ask(text, question, cfg=cfg.summary)
        if result.text:
            log.info("Antwort: %s", result.text)
        else:
            log.warning("Frage fehlgeschlagen: %s", result.error)
        return RunReport()

    jobs = get_jobs()
    started = jobs.start(f"Frage: {video.title}", job)
    if not started:
        return jsonify(ok=False, error=f"Läuft schon: {jobs.label}"), 409
    return jsonify(ok=True)
