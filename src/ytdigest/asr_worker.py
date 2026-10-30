"""ASR-Worker: isolierter HTTP-Dienst für den verteilten Betrieb (Web-UI/DB auf
einem Gerät, z. B. Raspberry Pi; ASR/Ollama auf dem GPU-Hauptrechner). Läuft
als eigener Prozess (``ytdigest asr-worker serve``) auf dem Hauptrechner und
bietet:

- ``POST /transcribe``: YouTube-/Podcast-URL transkribieren (der Worker lädt
  das Audio selbst herunter - kein Datei-Transfer nötig). Spiegelt
  ``sources/asr.py``/``sources/podcast.py``.
- ``GET /folders``, ``GET/POST /folders/<id>/files/...``: lesender Zugriff auf
  Ordner, die per ``asr_worker.allowed_roots`` (Glob-Muster) freigegeben sind -
  für ``local:<http-url>``-Zeilen in feeds.txt, siehe ``sources/local_remote.py``.

EXPERIMENTELL/eigenständig, lässt sich bei Bedarf komplett entfernen - betroffen
wären nur: diese Datei, der 'asr-worker'-Befehl in cli.py, AsrWorkerCfg in
config.py, AsrCfg.remote_url/remote_timeout_s und sources/local_remote.py.
"""

from __future__ import annotations

import glob
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request

from ytdigest.config import AsrWorkerCfg, Config
from ytdigest.sources import asr, local, podcast

log = logging.getLogger(__name__)


def _expanded_roots(cfg: AsrWorkerCfg) -> list[Path]:
    """Alle aktuell existierenden Ordner, die auf eines der konfigurierten
    Muster passen (z. B. ``~/Dokumente/sourcerer/*``) - wird bei jeder Anfrage
    neu ausgewertet, damit neue Unterordner ohne Worker-Neustart sichtbar
    werden."""
    roots: list[Path] = []
    for pattern in cfg.allowed_roots:
        for match in glob.glob(os.path.expanduser(pattern)):
            path = Path(match)
            if path.is_dir():
                roots.append(path.resolve())
    return roots


def _resolve_folder(cfg: AsrWorkerCfg, folder_id: str) -> Path | None:
    for root in _expanded_roots(cfg):
        if root.name == folder_id:
            return root
    return None


def _resolve_file(cfg: AsrWorkerCfg, folder_id: str, filename: str) -> Path | None:
    """Pfad zu ``filename`` in ``folder_id`` - None, wenn der Ordner nicht
    (mehr) freigegeben ist oder die aufgelöste Datei nicht direkt darin liegt
    (Schutz gegen Path-Traversal über '..' oder Symlinks)."""
    folder = _resolve_folder(cfg, folder_id)
    if folder is None:
        return None
    candidate = (folder / filename).resolve()
    if candidate.parent != folder:
        return None
    return candidate


def _transcript_result_json(result) -> dict:
    return {"text": result.text, "source": result.source,
            "language": result.language, "asr_model": result.asr_model}


def create_worker_app(cfg: Config) -> Flask:
    app = Flask(__name__)
    wcfg = cfg.asr_worker

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.get("/folders")
    def list_folders():
        return jsonify(folders=[{"id": root.name} for root in _expanded_roots(wcfg)])

    @app.get("/folders/<folder_id>/files")
    def list_files(folder_id):
        folder = _resolve_folder(wcfg, folder_id)
        if folder is None:
            return jsonify(error="unbekannter Ordner"), 404
        files = []
        for path in local.iter_video_files(folder, cfg.local.extensions):
            if not local.is_settled(path, min_age_s=cfg.local.min_age_s):
                continue
            files.append({
                "filename": path.name,
                "created_at": local.creation_time(path).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        return jsonify(files=files)

    @app.get("/folders/<folder_id>/files/<filename>/probe")
    def probe_file(folder_id, filename):
        path = _resolve_file(wcfg, folder_id, filename)
        if path is None:
            return jsonify(error="unbekannte Datei"), 404
        result = local.probe(path)
        return jsonify(duration_s=result.duration_s, error=result.error)

    @app.get("/folders/<folder_id>/files/<filename>/transcript")
    def existing_transcript(folder_id, filename):
        path = _resolve_file(wcfg, folder_id, filename)
        if path is None:
            return jsonify(error="unbekannte Datei"), 404
        result = local.read_existing_transcript(path)
        if result is None:
            return jsonify(error="kein Transkript vorhanden"), 404
        return jsonify(_transcript_result_json(result))

    @app.post("/folders/<folder_id>/files/<filename>/transcribe")
    def transcribe_file(folder_id, filename):
        path = _resolve_file(wcfg, folder_id, filename)
        if path is None:
            return jsonify(error="unbekannte Datei"), 404
        if not cfg.asr.enabled:
            return jsonify(error="ASR auf dem Worker deaktiviert"), 503
        try:
            result = asr.transcribe_local(path, cfg=cfg.asr)
        except asr.AsrUnavailable as exc:
            return jsonify(error=str(exc)), 503
        if result is None:
            return jsonify(error="kein Transkript ermittelbar"), 422
        return jsonify(_transcript_result_json(result))

    @app.post("/transcribe")
    def transcribe_remote():
        data = request.get_json(force=True, silent=True) or {}
        url = data.get("url")
        kind = data.get("kind", "youtube")
        if not url:
            return jsonify(error="'url' fehlt"), 400
        if not cfg.asr.enabled:
            return jsonify(error="ASR auf dem Worker deaktiviert"), 503
        try:
            if kind == "podcast":
                result = podcast.transcribe(url, cfg=cfg.asr, temp_dir=cfg.paths.temp_dir or None)
            else:
                result = asr.transcribe(url, cfg=cfg.asr, temp_dir=cfg.paths.temp_dir or None)
        except asr.AsrUnavailable as exc:
            return jsonify(error=str(exc)), 503
        if result is None:
            return jsonify(error="kein Transkript ermittelbar"), 422
        return jsonify(_transcript_result_json(result))

    return app
