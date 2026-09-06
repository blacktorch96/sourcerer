"""Fließtext in gut lesbare Zeilen umbrechen.

ASR liefert (und manche Caption-Spuren mit sehr langen Cues auch) einen
einzigen Absatz ohne Zeilenumbrüche. Für die Lesbarkeit wird auf eine
Zielbreite umgebrochen, dabei nie mitten im Wort getrennt und Zeilenenden
werden - wo möglich - an Satzgrenzen (. ! ?) gelegt statt mitten im Satz.
"""

from __future__ import annotations

import re
import textwrap

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def wrap_transcript(text: str, width: int = 120) -> str:
    """Text auf ``width`` Zeichen pro Zeile umbrechen, satzweise gepackt."""
    normalized = " ".join(text.split())
    if not normalized:
        return ""

    lines: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(normalized):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > width:
            # einzelner Satz zu lang: eigene Zeile(n), wortweise umgebrochen
            if current:
                lines.append(current)
                current = ""
            lines.extend(textwrap.wrap(sentence, width=width,
                                       break_long_words=False,
                                       break_on_hyphens=False))
            continue
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= width:
            current = candidate
        else:
            lines.append(current)
            current = sentence
    if current:
        lines.append(current)
    return "\n".join(lines)
