"""VTT nach Fließtext (spec 6.4).

Reine Zeitmarken (die genaue Cue-Millisekunde) sind für den Fließtext nicht
interessant und werden verworfen. Die groben Cue-*Abstände* werden dagegen
genutzt, um wie im ASR-Pfad (siehe asr.py) Absätze bei Sprechpausen zu bilden -
und, falls das Video echte YouTube-Kapitel hat, um sparsame Überschriften an
den Kapitelgrenzen einzufügen. Beides zusammen soll die Lesbarkeit erhöhen,
ohne die Ausgabe mit Markern zu überladen: Kapitel gibt es typischerweise nur
eine Handvoll pro Video, und an einer Kapitelgrenze wird kein zusätzlicher
Pausen-Absatz mehr gesetzt (die Überschrift trennt bereits).
"""

from __future__ import annotations

import bisect
import re

_TIMESTAMP_LINE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})"
)
_INLINE_TS = re.compile(r"<\d{2}:\d{2}:\d{2}[.,]\d{3}>")
_TAG = re.compile(r"</?c[^>]*>|</?[iub]>|<v[^>]*>|</v>")
_CUE_NUMBER = re.compile(r"^\d+$")
_META_LINE = re.compile(r"^(WEBVTT|Kind:|Language:|NOTE|STYLE|REGION)", re.IGNORECASE)


def _parse_ts(ts: str) -> float:
    h, m, s = ts.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _format_ts(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _sorted_chapters(chapters: list[dict] | None) -> list[dict]:
    """yt-dlp-Kapitelliste normalisieren: nur Einträge mit Titel und Startzeit,
    sortiert. Weniger als zwei Kapitel bringen keine Struktur, daher weg."""
    if not chapters:
        return []
    usable = [
        c for c in chapters
        if c.get("title") and c.get("start_time") is not None
    ]
    usable.sort(key=lambda c: c["start_time"])
    return usable if len(usable) >= 2 else []


def _chapter_index_at(chapters: list[dict], t: float) -> int:
    """Index des Kapitels, in dem Zeitpunkt ``t`` liegt (-1 vor dem ersten)."""
    starts = [c["start_time"] for c in chapters]
    return bisect.bisect_right(starts, t) - 1


def vtt_to_text(raw: str, *, pause_s: float = 1.8,
                chapters: list[dict] | None = None) -> str:
    chapters = _sorted_chapters(chapters)
    out: list[str] = []
    prev_text: str | None = None
    prev_end: float | None = None
    chapter_idx = -1
    cur_start: float | None = None
    cur_end: float | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _META_LINE.match(stripped):
            continue
        if "-->" in stripped:
            m = _TIMESTAMP_LINE.match(stripped)
            if m:
                cur_start, cur_end = _parse_ts(m.group(1)), _parse_ts(m.group(2))
                continue
        if _CUE_NUMBER.match(stripped):
            continue

        cleaned = _INLINE_TS.sub("", stripped)
        cleaned = _TAG.sub("", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        if not cleaned:
            continue

        # Rollierende Auto-Captions wiederholen denselben Text über mehrere
        # Cues, bis die naechste Phrase angehaengt wird. Ein Duplikat traegt
        # keinen neuen Inhalt, aber sein Cue-Ende ist die tatsaechliche
        # Sprechzeit - das muss fuer die Pausenerkennung erhalten bleiben,
        # sonst wirkt die reine Anzeigedauer der letzten Phrase wie eine
        # Sprechpause.
        if cleaned == prev_text:
            if cur_end is not None:
                prev_end = cur_end
            continue

        new_idx = -1
        if chapters and cur_start is not None:
            new_idx = _chapter_index_at(chapters, cur_start)
        if chapters and new_idx >= 0 and new_idx != chapter_idx:
            chapter = chapters[new_idx]
            if out:
                out.append("")
            out.append(f"## {_format_ts(chapter['start_time'])} {chapter['title']}")
            out.append("")
            chapter_idx = new_idx
        elif (pause_s > 0 and prev_end is not None and cur_start is not None
                and cur_start - prev_end >= pause_s and out and out[-1] != ""):
            out.append("")

        out.append(cleaned)
        prev_text = cleaned
        prev_end = cur_end

    return "\n".join(out).strip()
