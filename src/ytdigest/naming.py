"""Slug-Regeln (spec 7.2) und Kollisionsauflösung.

Strengste gemeinsame Teilmenge aus NTFS und ext4.
"""

from __future__ import annotations

import re
import unicodedata

_UMLAUTS = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "ß": "ss",
}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")
_MULTI_US = re.compile(r"_{2,}")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _transliterate(text: str) -> str:
    for src, dst in _UMLAUTS.items():
        text = text.replace(src, dst)
    return text


def slugify(text: str, *, max_len: int = 120) -> str:
    text = _transliterate(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _ILLEGAL.sub("", text)
    text = _WS.sub("_", text)
    text = _MULTI_US.sub("_", text)
    text = text.strip(". _")

    if text.upper() in _RESERVED:
        text = f"{text}_"

    if len(text) > max_len:
        text = text[:max_len]
        if "_" in text:
            text = text.rsplit("_", 1)[0]
        text = text.strip(". _")

    return text


def build_basename(published_at, title: str, *, max_len: int = 120,
                   video_id: str | None = None, force_id: bool = False) -> str:
    """``{YYYY-MM-DD_HHMMSS}_{title_slug}`` (Zeitstempel UTC)."""
    stamp = published_at.strftime("%Y-%m-%d_%H%M%S")
    title_slug = slugify(title, max_len=max_len) or (video_id or "video")
    base = f"{stamp}_{title_slug}"
    if force_id and video_id:
        base = f"{base}_{video_id}"
    return base


def resolve_collision(basename: str, video_id: str, existing: set[str]) -> str:
    """Bei Gleichstand ``_{video_id}`` anhängen (spec 7.2)."""
    if basename not in existing:
        return basename
    return f"{basename}_{video_id}"
