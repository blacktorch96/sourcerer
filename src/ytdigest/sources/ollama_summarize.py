"""Lokale Zusammenfassung über Ollama - EXPERIMENTELL.

Bewusst isoliert vom Rest der Pipeline (gleicher Schnitt wie sources/asr.py):
kennt nur SummaryCfg und reinen Text, keine DB, kein Dateisystem. Der Aufrufer
(web/summary_views.py) kümmert sich ums Ablegen als '*.summary.md' bzw.
'*.summary.error.txt'. Das ganze Feature ist testweise gebaut und könnte je
nach Ergebnis wieder komplett entfernt werden - daher der eigene Zuschnitt
statt einer Verzahnung mit pipeline.py/storage.py.
"""

from __future__ import annotations

import logging

import httpx

from ytdigest.config import SummaryCfg
from ytdigest.models import SummaryResult

log = logging.getLogger(__name__)


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def summarize(text: str, *, cfg: SummaryCfg) -> SummaryResult:
    """Ruft Ollamas '/api/generate' auf. Liefert bei jedem Fehler (Ollama
    nicht erreichbar, Timeout, leere Antwort, ...) ein SummaryResult mit
    gesetztem ``error`` statt eine Exception zu werfen - der Aufrufer legt
    diesen Text unverändert als '*.summary.error.txt' ab."""
    body, truncated = _truncate(text, cfg.max_input_chars)
    if truncated:
        log.warning(
            "Transkript für die Zusammenfassung auf %d Zeichen gekürzt "
            "(cfg.summary.max_input_chars) - Ergebnis kann dadurch unvollständig sein.",
            cfg.max_input_chars,
        )
    prompt = cfg.prompt_template.format(text=body, max_chars=cfg.max_chars)

    try:
        resp = httpx.post(
            f"{cfg.ollama_host.rstrip('/')}/api/generate",
            json={
                "model": cfg.model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_ctx": cfg.num_ctx},
            },
            timeout=cfg.timeout_s,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return SummaryResult(
            text=None,
            error=f"Ollama nicht erreichbar ({cfg.ollama_host}, Modell {cfg.model}): {exc}",
        )

    try:
        data = resp.json()
    except ValueError as exc:
        return SummaryResult(text=None, error=f"Ungültige Antwort von Ollama: {exc}")

    summary = (data.get("response") or "").strip()
    if not summary:
        return SummaryResult(text=None, error="Ollama lieferte eine leere Antwort")

    log.info("Zusammenfassung erzeugt (%d Zeichen%s)",
             len(summary), ", Transkript war gekürzt" if truncated else "")
    return SummaryResult(text=summary, truncated=truncated)


def list_models(cfg: SummaryCfg) -> list[str]:
    """Lokal in Ollama installierte Modelle (für die Auswahl auf der
    Einstellungsseite) - leere Liste, wenn Ollama nicht erreichbar ist; der
    Aufrufer fällt dann auf ein Freitextfeld zurück statt einer Auswahlliste."""
    try:
        resp = httpx.get(f"{cfg.ollama_host.rstrip('/')}/api/tags", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Ollama-Modellliste nicht abrufbar (%s): %s", cfg.ollama_host, exc)
        return []
    names = [m.get("name") for m in data.get("models", []) if m.get("name")]
    return sorted(names)
