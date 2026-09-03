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


def _try_source(available: dict[str, list], tag: str, languages: list[str],
                ) -> TranscriptResult | None:
    for lang in languages:
        code = _match_lang(available, lang)
        if code is None:
            continue
        text = _download_track(available[code])
        if not text:
            continue
        body = vtt_to_text(text)
        if body:
            return TranscriptResult(text=body, source=tag, language=lang)
    return None


def fetch_captions(probe_result: VideoProbe, languages: list[str], *,
                   prefer_manual: bool = True) -> TranscriptResult | None:
    info = probe_result.info
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    if prefer_manual:
        # alle Sprachen manuell, danach alle Sprachen auto
        return (_try_source(manual, "captions_manual", languages)
                or _try_source(auto, "captions_auto", languages))

    # je Sprache erst manuell, dann auto
    for lang in languages:
        result = (_try_source(manual, "captions_manual", [lang])
                  or _try_source(auto, "captions_auto", [lang]))
        if result:
            return result
    return None
