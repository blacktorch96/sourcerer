"""Zusammenfassung/Fragen über openrouter.ai - EXTERNE Verarbeitung.

Alternative zu sources/ollama_summarize.py: derselbe Prompt (siehe
build_summary_prompt/build_ask_prompt), aber das Transkript wird dabei an
einen Drittanbieter geschickt. Gleicher Zuschnitt wie dort: kennt nur Config
und Text, keine DB, kein Dateisystem, wirft keine Exceptions, sondern liefert
ein SummaryResult mit ``error``. Der API-Schlüssel taucht in keiner
Fehlermeldung oder Logzeile auf.
"""

from __future__ import annotations

import logging

import httpx

from ytdigest.config import OpenRouterCfg, SummaryCfg
from ytdigest.models import SummaryResult
from ytdigest.sources import ollama_summarize

log = logging.getLogger(__name__)


def _headers(cfg: OpenRouterCfg) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.resolved_api_key()}", "X-Title": "ytdigest"}


def _api_error(data: object) -> str | None:
    """Fehlertext aus ``{"error": {"message": ..., "metadata": {...}}}``
    (OpenRouter liefert solche Antworten auch mit HTTP 200). Bei "Provider
    returned error" steckt die eigentliche Ursache in metadata.raw."""
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        if not isinstance(err, dict):
            return str(err)
        msg = str(err.get("message") or err)
        meta = err.get("metadata")
        if isinstance(meta, dict):
            extra = " ".join(str(meta[k])[:300] for k in ("provider_name", "raw") if meta.get(k))
            if extra:
                msg += f" ({extra})"
        return msg
    return None


def _generate(prompt: str, *, cfg: OpenRouterCfg) -> SummaryResult:
    if not cfg.configured:
        return SummaryResult(text=None, error="OpenRouter ist nicht konfiguriert "
                                              "(API-Schlüssel oder Modell fehlt)")
    try:
        resp = httpx.post(
            f"{cfg.base_url.rstrip('/')}/chat/completions",
            headers=_headers(cfg),
            json={"model": cfg.model, "messages": [{"role": "user", "content": prompt}]},
            timeout=cfg.timeout_s,
        )
    except httpx.HTTPError as exc:
        return SummaryResult(text=None, error=f"OpenRouter nicht erreichbar: {exc}")

    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code >= 400 or _api_error(data):
        detail = _api_error(data) or resp.text[:200]
        if resp.status_code == 429:
            detail += (" - Rate-Limit: bei kostenlosen (:free) Modellen oft ausgelastet; "
                       "später erneut versuchen oder ein bezahltes Modell wählen")
        return SummaryResult(
            text=None,
            error=f"OpenRouter-Fehler (HTTP {resp.status_code}, Modell {cfg.model}): {detail}",
        )

    try:
        answer = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return SummaryResult(text=None, error="Ungültige Antwort von OpenRouter")
    if not answer:
        return SummaryResult(text=None, error="OpenRouter lieferte eine leere Antwort")
    return SummaryResult(text=answer)


def summarize(text: str, *, cfg: SummaryCfg, or_cfg: OpenRouterCfg) -> SummaryResult:
    prompt, truncated = ollama_summarize.build_summary_prompt(text, cfg=cfg)
    result = _generate(prompt, cfg=or_cfg)
    if result.text:
        result.truncated = truncated
        log.info("Zusammenfassung erzeugt via OpenRouter (%d Zeichen)", len(result.text))
    return result


def ask(text: str, question: str, *, cfg: SummaryCfg, or_cfg: OpenRouterCfg) -> SummaryResult:
    prompt, truncated = ollama_summarize.build_ask_prompt(text, question, cfg=cfg)
    result = _generate(prompt, cfg=or_cfg)
    if result.text:
        result.truncated = truncated
        log.info("Antwort erzeugt via OpenRouter (%d Zeichen)", len(result.text))
    return result


def check_connection(cfg: OpenRouterCfg) -> tuple[bool, str]:
    """Prüft den API-Schlüssel (GET /key) für den Verbindungstest auf der
    Einstellungsseite. Liefert (ok, Meldung)."""
    if not cfg.resolved_api_key():
        return False, "Kein API-Schlüssel hinterlegt."
    try:
        resp = httpx.get(f"{cfg.base_url.rstrip('/')}/key", headers=_headers(cfg), timeout=10.0)
    except httpx.HTTPError as exc:
        return False, f"OpenRouter nicht erreichbar: {exc}"
    if resp.status_code in (401, 403):
        return False, "API-Schlüssel wurde von OpenRouter abgelehnt."
    if resp.status_code >= 400:
        return False, f"OpenRouter antwortet mit HTTP {resp.status_code}."
    try:
        info = resp.json().get("data") or {}
    except (ValueError, AttributeError):
        info = {}
    label = info.get("label")
    remaining = info.get("limit_remaining")
    msg = "Verbindung zu OpenRouter steht"
    if label:
        msg += f" (Schlüssel „{label}“)"
    if isinstance(remaining, int | float):
        msg += f", Restguthaben/-limit: {remaining:.2f} $"
    return True, msg + "."
