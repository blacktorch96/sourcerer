"""Podcast-Episoden: Audio-Enclosure per HTTP laden, immer per ASR
transkribieren (kein Caption-Pfad, Podcasts haben keine YouTube-Untertitel).

Analog zu ``local.py`` für lokale Videodateien, nur dass die Quelldatei erst
heruntergeladen werden muss statt schon auf der Platte zu liegen.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

import httpx

from ytdigest.config import AsrCfg
from ytdigest.models import TranscriptResult, VideoProbe
from ytdigest.sources import asr

log = logging.getLogger(__name__)

PODCAST_CHANNEL_PREFIX = "podcast:"
_DOWNLOAD_TIMEOUT_S = 300.0
# Hosts wie Buzzsprout blocken den httpx-Standard-User-Agent ("python-httpx/…")
# mit 403 - ein browserähnlicher UA kommt durch.
_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def channel_id_for(feed_url: str) -> str:
    """Stabile, eindeutige ``channel_id`` für einen Podcast-Feed - die
    RSS-URL selbst hat keine YouTube-artige Kanal-ID."""
    return f"{PODCAST_CHANNEL_PREFIX}{feed_url}"


def is_podcast_feed(channel_id: str) -> bool:
    return channel_id.startswith(PODCAST_CHANNEL_PREFIX)


def probe(duration_s: int | None) -> VideoProbe:
    """Dauer kommt, falls vorhanden, schon aus ``itunes:duration`` im Feed
    (siehe ``feeds/podcast_fetcher.py``) - kein Netzwerkzugriff nötig."""
    return VideoProbe(duration_s=duration_s)


def _download_audio(url: str, workdir: Path) -> Path:
    suffix = Path(url.split("?", 1)[0]).suffix or ".audio"
    target = workdir / f"episode{suffix}"
    with httpx.stream(
        "GET", url, headers=_DOWNLOAD_HEADERS,
        timeout=_DOWNLOAD_TIMEOUT_S, follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        with open(target, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    return target


def transcribe(url: str, *, cfg: AsrCfg, temp_dir: str | None) -> TranscriptResult | None:
    """Episode laden und wie eine lokale Datei per faster-whisper
    transkribieren (``asr.transcribe_local`` demuxt selbst per ffmpeg)."""
    asr._require_faster_whisper()  # vor dem Download prüfen, nicht erst danach
    workdir = Path(tempfile.mkdtemp(prefix="ytdigest_podcast_", dir=temp_dir or None))
    try:
        audio_path = _download_audio(url, workdir)
        return asr.transcribe_local(audio_path, cfg=cfg)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
