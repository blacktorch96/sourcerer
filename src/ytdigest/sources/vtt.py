"""VTT nach Fließtext (spec 6.4). Zeitmarken werden bewusst verworfen."""

from __future__ import annotations

import re

_TIMESTAMP_LINE = re.compile(
    r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}"
)
_INLINE_TS = re.compile(r"<\d{2}:\d{2}:\d{2}[.,]\d{3}>")
_TAG = re.compile(r"</?c[^>]*>|</?[iub]>|<v[^>]*>|</v>")
_CUE_NUMBER = re.compile(r"^\d+$")
_META_LINE = re.compile(r"^(WEBVTT|Kind:|Language:|NOTE|STYLE|REGION)", re.IGNORECASE)


def vtt_to_text(raw: str) -> str:
    out: list[str] = []
    prev = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _META_LINE.match(stripped):
            continue
        if "-->" in stripped and _TIMESTAMP_LINE.match(stripped):
            continue
        if _CUE_NUMBER.match(stripped):
            continue

        cleaned = _INLINE_TS.sub("", stripped)
        cleaned = _TAG.sub("", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        if not cleaned:
            continue
        if cleaned == prev:  # direkt aufeinanderfolgende identische Zeilen
            continue
        out.append(cleaned)
        prev = cleaned

    return "\n".join(out).strip()
