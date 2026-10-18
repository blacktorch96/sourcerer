"""Lokale Videodateien: Verzeichnis-Scan, Dauer/Erstellungsdatum per ffprobe.

Layout-Annahme (siehe README): unter einem Wurzelverzeichnis liegt je
Unterordner ein "Kanal" (Ordnername = channel_id/dir_slug), Videodateien
direkt darin. Alternativ kann ein einzelner Ordner per ``local:<pfad>`` in
feeds.txt direkt als Feed eingetragen werden (siehe ``feeds/parser.py``,
``pipeline.sync``) - dort liegen die Dateien ohne Kanal-Unterordner direkt
im angegebenen Verzeichnis.

Standardmäßig läuft jede gefundene Datei über ASR (siehe asr.py,
``transcribe_local``) - außer es liegt bereits eine gleichnamige ``.txt``
daneben (siehe ``read_existing_transcript``), dann wird die übernommen.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from ytdigest.models import TranscriptResult, VideoProbe

log = logging.getLogger(__name__)

LOCAL_FEED_PREFIX = "local:"
_FFPROBE_TIMEOUT_S = 30


def feed_url_for(channel_dir: Path) -> str:
    """Stabile, eindeutige ``feed_url`` für den synthetischen Feed-Eintrag
    eines lokalen Kanalordners (erfüllt die UNIQUE-Constraint in der DB)."""
    return f"{LOCAL_FEED_PREFIX}{channel_dir.resolve()}"


def is_local_feed(feed_url: str) -> bool:
    return feed_url.startswith(LOCAL_FEED_PREFIX)


def parse_local_path(raw: str) -> Path:
    """Pfad aus einer ``local:``-Zeile in feeds.txt - akzeptiert sowohl
    Windows- (``C:\\Users\\...``) als auch Linux/macOS-Pfade (``/home/...``),
    unabhängig vom Betriebssystem, auf dem feeds.txt gerade geparst wird.
    Ob der Pfad auf diesem Rechner tatsächlich existiert, prüft erst der
    Scan - ein Windows-Pfad in einer über mehrere Geräte synchronisierten
    feeds.txt ist z. B. unter macOS schlicht nicht vorhanden und wird dort
    übersprungen (siehe ``pipeline.sync``)."""
    text = raw.strip()
    if "\\" in text:
        text = text.replace("\\", "/")
    return Path(text)


def existing_transcript_path(media_path: Path) -> Path | None:
    """Pfad zu einer bereits vorhandenen ``.txt`` neben ``media_path`` (gleicher
    Dateiname ohne Endung) - z. B. von Hand erstellte oder aus einer anderen
    Quelle übernommene Transkripte. Wird eine gefunden, läuft die Datei nie
    durch ASR."""
    candidate = media_path.with_suffix(".txt")
    return candidate if candidate.is_file() else None


def read_existing_transcript(media_path: Path) -> TranscriptResult | None:
    txt_path = existing_transcript_path(media_path)
    if txt_path is None:
        return None
    try:
        text = txt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("Vorhandene Transkript-Datei nicht lesbar (%s): %s", txt_path, exc)
        return None
    if not text:
        return None
    log.info("Vorhandenes Transkript übernommen: %s", txt_path)
    return TranscriptResult(text=text, source="local_txt", language="")


def iter_channel_dirs(root: Path) -> Iterator[Path]:
    """Direkte Unterordner von ``root`` - je einer ein Kanal."""
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            yield entry


def iter_video_files(channel_dir: Path, extensions: list[str]) -> Iterator[Path]:
    exts = {f".{e.lstrip('.').lower()}" for e in extensions}
    for entry in sorted(channel_dir.iterdir(), key=lambda p: p.name):
        if entry.is_file() and entry.suffix.lower() in exts:
            yield entry


def is_settled(path: Path, *, min_age_s: int) -> bool:
    """True, wenn die Datei seit mindestens ``min_age_s`` Sekunden nicht mehr
    verändert wurde - Heuristik gegen das Anfassen einer noch laufenden
    Kopie/Export. Eine noch zu junge Datei wird beim nächsten Scan erneut
    geprüft, es gibt keine blockierende Wartezeit innerhalb eines Laufs."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return (time.time() - mtime) >= min_age_s


def _run_ffprobe(path: Path, entries: str) -> dict | None:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", entries,
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT_S, check=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.warning("ffprobe fehlgeschlagen für %s: %s", path, exc)
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _ffprobe_duration(path: Path) -> int | None:
    data = _run_ffprobe(path, "format=duration")
    if data is None:
        return None
    try:
        return int(float(data["format"]["duration"]))
    except (KeyError, ValueError, TypeError):
        return None


def _ffprobe_creation_time(path: Path) -> datetime | None:
    data = _run_ffprobe(path, "format_tags=creation_time")
    if data is None:
        return None
    raw = data.get("format", {}).get("tags", {}).get("creation_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def creation_time(path: Path) -> datetime:
    """Bestmögliches Erstellungsdatum für ``published_at``: zuerst das in den
    Container-Metadaten eingebettete ``creation_time`` (meist das tatsächliche
    Aufnahme-/Exportdatum, unabhängig vom Dateisystem), sonst die
    Datei-Erstellungszeit. Unter Windows ist ``st_ctime`` die Erstellungszeit;
    unter Linux/macOS ohne ``st_birthtime`` ist es nur die letzte
    Metadatenänderung - dort also ein Fallback zweiter Wahl, kein echtes
    Geburtsdatum."""
    embedded = _ffprobe_creation_time(path)
    if embedded is not None:
        return embedded
    st = path.stat()
    ts = getattr(st, "st_birthtime", None)
    if ts is None:
        ts = st.st_ctime
    return datetime.fromtimestamp(ts, tz=UTC)


def probe(path: Path) -> VideoProbe:
    if not path.is_file():
        return VideoProbe(duration_s=None, error="missing")
    duration = _ffprobe_duration(path)
    if duration is None:
        return VideoProbe(duration_s=None, error="ffprobe_failed")
    return VideoProbe(duration_s=duration)
