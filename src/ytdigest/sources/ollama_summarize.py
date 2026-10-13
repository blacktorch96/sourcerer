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


def _generate(prompt: str, *, cfg: SummaryCfg) -> SummaryResult:
    """Gemeinsamer Ollama-Aufruf für summarize() und ask(). Liefert bei jedem
    Fehler (Ollama nicht erreichbar, Timeout, leere Antwort, ...) ein
    SummaryResult mit gesetztem ``error`` statt eine Exception zu werfen."""
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

    answer = (data.get("response") or "").strip()
    if not answer:
        return SummaryResult(text=None, error="Ollama lieferte eine leere Antwort")
    return SummaryResult(text=answer)


def summarize(text: str, *, cfg: SummaryCfg) -> SummaryResult:
    body, truncated = _truncate(text, cfg.max_input_chars)
    if truncated:
        log.warning(
            "Transkript für die Zusammenfassung auf %d Zeichen gekürzt "
            "(cfg.summary.max_input_chars) - Ergebnis kann dadurch unvollständig sein.",
            cfg.max_input_chars,
        )
    prompt = cfg.prompt_template.format(text=body, max_chars=cfg.max_chars)
    result = _generate(prompt, cfg=cfg)
    if result.text:
        result.truncated = truncated
        log.info("Zusammenfassung erzeugt (%d Zeichen%s)",
                 len(result.text), ", Transkript war gekürzt" if truncated else "")
    return result


def ask(text: str, question: str, *, cfg: SummaryCfg) -> SummaryResult:
    """Wie summarize(), aber für eine freie Frage zum Transkript statt einer
    festen Zusammenfassung - gleicher Ollama-Aufruf, eigenes Prompt-Template
    (cfg.ask_prompt_template)."""
    body, truncated = _truncate(text, cfg.max_input_chars)
    if truncated:
        log.warning(
            "Transkript für die Frage auf %d Zeichen gekürzt "
            "(cfg.summary.max_input_chars) - Antwort kann dadurch unvollständig sein.",
            cfg.max_input_chars,
        )
    prompt = cfg.ask_prompt_template.format(text=body, question=question)
    result = _generate(prompt, cfg=cfg)
    if result.text:
        result.truncated = truncated
        log.info("Antwort erzeugt (%d Zeichen%s)",
                 len(result.text), ", Transkript war gekürzt" if truncated else "")
    return result


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
