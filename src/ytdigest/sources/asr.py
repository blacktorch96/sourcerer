"""Audio-Download plus faster-whisper. Isoliert die schwergewichtige ASR-Abhängigkeit."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from ytdigest.config import AsrCfg
from ytdigest.models import TranscriptResult

log = logging.getLogger(__name__)


class AsrUnavailable(RuntimeError):
    """faster-whisper ist nicht installiert (Extra 'asr' fehlt)."""


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _download_audio(url: str, workdir: Path) -> Path:
    from yt_dlp import YoutubeDL

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "audio.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        candidate = Path(ydl.prepare_filename(info))
    if candidate.exists():
        return candidate
    files = [p for p in workdir.iterdir() if p.is_file()]
    if not files:
        raise RuntimeError("Audio-Download lieferte keine Datei")
    return files[0]


def _to_paragraphs(segments, *, pause_s: float) -> str:
    """Segmente zu Fließtext zusammenfügen, mit Absatzumbruch bei Sprechpausen.

    faster-whisper liefert je Segment ``start``/``end`` in Sekunden. Eine
    Lücke von mindestens ``pause_s`` zwischen zwei Segmenten deutet meist auf
    einen Themen-/Sprecherwechsel hin und wird als Absatzgrenze (Leerzeile)
    übernommen statt die Segmente einfach zu einem Block zusammenzukleben.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    prev_end: float | None = None
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if (pause_s > 0 and prev_end is not None
                and seg.start - prev_end >= pause_s and current):
            paragraphs.append(" ".join(current))
            current = []
        current.append(text)
        prev_end = seg.end
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs).strip()


def _require_faster_whisper() -> None:
    try:
        import faster_whisper  # noqa: F401
    except ImportError as exc:  # pragma: no cover - abhängig von der Installation
        raise AsrUnavailable(
            "faster-whisper fehlt. Installation mit dem Extra 'asr'."
        ) from exc


def _run_whisper(media_path: Path, cfg: AsrCfg) -> TranscriptResult | None:
    """faster-whisper auf eine bereits lokal vorliegende Mediendatei ansetzen -
    gemeinsamer Kern für den YouTube- (nach Audio-Download) und den lokalen
    Pfad (direkt auf der Videodatei, faster-whisper demuxt selbst per ffmpeg)."""
    from faster_whisper import WhisperModel

    device = _resolve_device(cfg.device)
    compute_type = cfg.compute_type if device == "cuda" else "int8"
    log.info("ASR: %s auf %s (%s)", cfg.model, device, compute_type)

    model = WhisperModel(cfg.model, device=device, compute_type=compute_type)
    segments, info = model.transcribe(str(media_path), beam_size=cfg.beam_size)
    text = _to_paragraphs(segments, pause_s=cfg.paragraph_pause_s)
    if not text:
        return None
    return TranscriptResult(
        text=text,
        source="asr",
        language=getattr(info, "language", "") or "",
        asr_model=cfg.model,
    )


def transcribe(url: str, *, cfg: AsrCfg, temp_dir: str | None) -> TranscriptResult | None:
    _require_faster_whisper()
    workdir = Path(tempfile.mkdtemp(prefix="ytdigest_asr_", dir=temp_dir or None))
    try:
        audio_path = _download_audio(url, workdir)
        return _run_whisper(audio_path, cfg)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def transcribe_local(path: Path, *, cfg: AsrCfg) -> TranscriptResult | None:
    """Wie ``transcribe``, aber ohne den yt-dlp-Downloadumweg: die lokale
    Videodatei liegt schon vor, faster-whisper liest sie direkt."""
    _require_faster_whisper()
    return _run_whisper(path, cfg)
