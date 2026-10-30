"""Client für Ordner, die über den ASR-Worker (siehe ``asr_worker.py``) auf
einem anderen Rechner freigegeben sind - für ``local:<http(s)-url>``-Zeilen in
feeds.txt. Spiegelt die Funktionen aus ``local.py`` 1:1, nur per HTTP statt
direktem Dateisystemzugriff: die Pipeline fasst diese Dateien hier nie selbst
im Dateisystem an.

EXPERIMENTELL/eigenständig, lässt sich bei Bedarf komplett entfernen -
betroffen wären nur: diese Datei, ``asr_worker.py``, ``ParsedFeedLine.local_remote_url``
(models.py, feeds/parser.py) und der ``is_remote_local``-Zweig in pipeline.py.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from ytdigest.models import TranscriptResult, VideoProbe
from ytdigest.sources.asr import AsrUnavailable

log = logging.getLogger(__name__)

REMOTE_LOCAL_FEED_PREFIX = "local-remote:"
_REQUEST_TIMEOUT_S = 30.0


class RemoteFolderUnavailable(RuntimeError):
    """Worker oder Ordner nicht erreichbar - wird bewusst NICHT in
    pipeline._try_asr abgefangen, damit Sync/Verarbeitung das wie einen
    normalen, später per Retry wiederholbaren Fehler behandelt."""


def feed_url_for(folder_url: str) -> str:
    return f"{REMOTE_LOCAL_FEED_PREFIX}{folder_url}"


def is_remote_local_feed(feed_url: str) -> bool:
    return feed_url.startswith(REMOTE_LOCAL_FEED_PREFIX)


def folder_id_from_url(folder_url: str) -> str:
    return folder_url.rstrip("/").rsplit("/", 1)[-1]


def file_url_for(folder_url: str, filename: str) -> str:
    return f"{folder_url.rstrip('/')}/files/{filename}"


def _get(url: str) -> httpx.Response:
    try:
        return httpx.get(url, timeout=_REQUEST_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise RemoteFolderUnavailable(f"ASR-Worker nicht erreichbar ({url}): {exc}") from exc


def list_files(folder_url: str) -> list[dict]:
    """Neue, "settled" Dateien im freigegebenen Ordner - Pendant zu
    ``local.iter_video_files``/``local.is_settled``/``local.creation_time``,
    vom Worker bereits kombiniert ausgewertet."""
    resp = _get(f"{folder_url.rstrip('/')}/files")
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json().get("files", [])


def probe(file_url: str) -> VideoProbe:
    resp = _get(f"{file_url}/probe")
    if resp.status_code == 404:
        return VideoProbe(duration_s=None, error="missing")
    resp.raise_for_status()
    data = resp.json()
    return VideoProbe(duration_s=data.get("duration_s"), error=data.get("error"))


def read_existing_transcript(file_url: str) -> TranscriptResult | None:
    resp = _get(f"{file_url}/transcript")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return TranscriptResult(text=data["text"], source=data["source"],
                            language=data.get("language", ""))


def transcribe(file_url: str, *, timeout_s: float) -> TranscriptResult | None:
    try:
        resp = httpx.post(f"{file_url}/transcribe", timeout=timeout_s)
    except httpx.HTTPError as exc:
        raise RemoteFolderUnavailable(
            f"ASR-Worker nicht erreichbar ({file_url}): {exc}") from exc
    if resp.status_code == 503:
        raise AsrUnavailable(resp.json().get("error", "ASR-Worker: ASR nicht verfügbar"))
    if resp.status_code == 422:
        return None
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return TranscriptResult(text=data["text"], source=data["source"],
                            language=data.get("language", ""), asr_model=data.get("asr_model"))


def parse_created_at(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
