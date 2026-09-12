"""yt-dlp: Metadatenabruf und Untertitelbeschaffung mit Sprachauswahl."""

from __future__ import annotations

import logging

import httpx

from ytdigest.models import TranscriptResult, VideoProbe
from ytdigest.sources.vtt import vtt_to_text

log = logging.getLogger(__name__)

_YDL_OPTS = {
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "extract_flat": False,
}


def _classify_error(message: str) -> str:
    low = message.lower()
    if "private" in low:
        return "private"
    if "age" in low and "restrict" in low:
        return "age_restricted"
    if "rate" in low or "429" in low or "too many requests" in low:
        return "rate_limited"
    if any(s in low for s in ("unavailable", "removed", "deleted", "not available",
                              "no longer", "terminated")):
        return "unavailable"
    return "unknown"


def _extract_info(url: str) -> dict:
    from yt_dlp import YoutubeDL  # lazy: yt-dlp ist schwergewichtig

    with YoutubeDL(_YDL_OPTS) as ydl:
        return ydl.extract_info(url, download=False)


_ORIG_SUFFIX = "-orig"


def _base_lang(code: str) -> str:
    return code.split("-")[0].split("_")[0].lower()


def _detect_original_lang(info: dict) -> str | None:
    """Tatsächliche Sprache des Videos ermitteln (spec: Original vor Präferenz).

    yt-dlp markiert die unübersetzte automatische Untertitelspur mit dem
    Suffix "-orig" (z. B. "en-orig"); alle anderen Sprachcodes unter
    automatic_captions sind on-the-fly-Übersetzungen (tlang=...) und sagen
    nichts über die tatsächlich gesprochene Sprache aus.

    Gelegentlich mergt yt-dlp Antworten mehrerer interner Player-Clients und
    liefert dann mehrere "-orig"-Kandidaten gleichzeitig. In dem Fall nur
    zuschlagen, wenn das (unabhängige) yt-dlp-Feld "language" einen davon
    bestätigt - sonst lieber None (und damit ASR-Fallback) als raten.
    """
    auto = info.get("automatic_captions") or {}
    orig_codes = [code[: -len(_ORIG_SUFFIX)] for code in auto if code.endswith(_ORIG_SUFFIX)]
    top_lang = info.get("language")
    top_base = _base_lang(top_lang) if top_lang else None

    if len(orig_codes) == 1:
        return orig_codes[0]
    if orig_codes:
        for code in orig_codes:
            if top_base and _base_lang(code) == top_base:
                return code
        return None
    return top_base


def probe(url: str) -> VideoProbe:
    from yt_dlp.utils import DownloadError

    try:
        info = _extract_info(url)
    except DownloadError as exc:
        cause = _classify_error(str(exc))
        log.warning("Metadatenabruf fehlgeschlagen (%s): %s", cause, url)
        return VideoProbe(duration_s=None, error=cause)
    except Exception as exc:  # noqa: BLE001 - yt-dlp wirft breit
        log.warning("Metadatenabruf-Ausnahme: %s", exc)
        return VideoProbe(duration_s=None, error="unknown")

    duration = info.get("duration")
    return VideoProbe(
        duration_s=int(duration) if duration else None,
        manual_langs=sorted((info.get("subtitles") or {}).keys()),
        auto_langs=sorted((info.get("automatic_captions") or {}).keys()),
        original_lang=_detect_original_lang(info),
        info=info,
    )


def _match_lang(available: dict[str, list], wanted: str) -> str | None:
    if wanted in available:
        return wanted
    for code in available:
        if code.split("-")[0].split(".")[0] == wanted:
            return code
    return None


def _download_track(tracks: list[dict]) -> str | None:
    preferred = sorted(
        tracks,
        key=lambda t: {"vtt": 0, "srv3": 1, "srv1": 2}.get(t.get("ext", ""), 5),
    )
    for track in preferred:
        url = track.get("url")
        if not url:
            continue
        try:
            resp = httpx.get(url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Untertiteldownload fehlgeschlagen: %s", exc)
            continue
        if track.get("ext") == "vtt" or "WEBVTT" in resp.text[:64]:
            return resp.text
    return None


def _try_source(available: dict[str, list], tag: str, languages: list[str], *,
                pause_s: float, chapters: list[dict] | None) -> TranscriptResult | None:
    for lang in languages:
        code = _match_lang(available, lang)
        if code is None:
            continue
        text = _download_track(available[code])
        if not text:
            continue
        body = vtt_to_text(text, pause_s=pause_s, chapters=chapters)
        if body:
            return TranscriptResult(text=body, source=tag, language=lang)
    return None


def _fetch_original(probe_result: VideoProbe, *, pause_s: float,
                    chapters: list[dict] | None) -> TranscriptResult | None:
    """Nur die tatsächliche Sprache des Videos versuchen (manuell, dann echte
    ASR-Originalspur) - niemals eine auto-übersetzte Spur. Liefert None,
    wenn die Originalsprache nicht als Untertitel verfügbar ist; der Aufrufer
    fällt dann auf lokales ASR zurück (das die Sprache selbst erkennt)."""
    original = probe_result.original_lang
    if not original:
        return None
    info = probe_result.info
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    result = _try_source(manual, "captions_manual", [original],
                         pause_s=pause_s, chapters=chapters)
    if result:
        return result

    orig_key = f"{original}{_ORIG_SUFFIX}"
    if orig_key in auto:
        text = _download_track(auto[orig_key])
        if text:
            body = vtt_to_text(text, pause_s=pause_s, chapters=chapters)
            if body:
                return TranscriptResult(text=body, source="captions_auto", language=original)
    elif not any(code.endswith(_ORIG_SUFFIX) for code in auto) and original in auto:
        # Kein Video mit -orig-Markierung (altes/anderes Feed-Format) - der
        # unübersetzte Code kann hier noch vertrauenswürdig sein.
        result = _try_source(auto, "captions_auto", [original],
                             pause_s=pause_s, chapters=chapters)
        if result:
            return result
    return None


def fetch_captions(probe_result: VideoProbe, languages: list[str], *,
                   prefer_manual: bool = True,
                   prefer_original: bool = True,
                   paragraph_pause_s: float = 1.8,
                   chapter_headings: bool = True) -> TranscriptResult | None:
    chapters = probe_result.info.get("chapters") if chapter_headings else None

    if prefer_original:
        result = _fetch_original(probe_result, pause_s=paragraph_pause_s, chapters=chapters)
        if result:
            return result
        # Originalsprache nicht als Untertitel verfügbar: nicht auf eine
        # übersetzte Spur ausweichen, sondern None liefern, damit die
        # Pipeline auf lokales ASR zurückfällt (spec: immer Originalsprache).
        return None

    info = probe_result.info
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    if prefer_manual:
        # alle Sprachen manuell, danach alle Sprachen auto
        return (_try_source(manual, "captions_manual", languages,
                            pause_s=paragraph_pause_s, chapters=chapters)
                or _try_source(auto, "captions_auto", languages,
                               pause_s=paragraph_pause_s, chapters=chapters))

    # je Sprache erst manuell, dann auto
    for lang in languages:
        result = (_try_source(manual, "captions_manual", [lang],
                              pause_s=paragraph_pause_s, chapters=chapters)
                  or _try_source(auto, "captions_auto", [lang],
                                 pause_s=paragraph_pause_s, chapters=chapters))
        if result:
            return result
    return None
