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


def _to_paragraphs(segments) -> str:
    parts: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def transcribe(url: str, *, cfg: AsrCfg, temp_dir: str | None) -> TranscriptResult | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - abhängig von der Installation
        raise AsrUnavailable(
            "faster-whisper fehlt. Installation mit dem Extra 'asr'."
        ) from exc

    workdir = Path(tempfile.mkdtemp(prefix="ytdigest_asr_", dir=temp_dir or None))
    try:
        audio_path = _download_audio(url, workdir)
        device = _resolve_device(cfg.device)
        compute_type = cfg.compute_type if device == "cuda" else "int8"
        log.info("ASR: %s auf %s (%s)", cfg.model, device, compute_type)

        model = WhisperModel(cfg.model, device=device, compute_type=compute_type)
        segments, info = model.transcribe(str(audio_path), beam_size=cfg.beam_size)
        text = _to_paragraphs(segments)
        if not text:
            return None
        return TranscriptResult(
            text=text,
            source="asr",
            language=getattr(info, "language", "") or "",
            asr_model=cfg.model,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
