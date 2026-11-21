"""Einstellungsseite: Dauerfilter (filters.min_duration_min) sowie Ollama-Modell
(summary.model) und Prompt (summary.prompt_template) für die Zusammenfassung sowie
die Anbindung an OpenRouter (externer Dienst, Abschnitt [openrouter]).

Schreibt gezielt in config.toml (config_writer.py, kommentarerhaltend) und
aktualisiert zusätzlich das im Prozess gehaltene Config-Objekt direkt, damit
eine Änderung sofort wirkt statt erst nach einem Neustart von
'ytdigest web serve'.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ytdigest import config_writer
from ytdigest.config import SummaryCfg
from ytdigest.sources import ollama_summarize, openrouter
from ytdigest.web.context import get_cfg

log = logging.getLogger(__name__)

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _config_path() -> Path:
    cfg = get_cfg()
    return cfg.source_path or Path("config.toml")


@bp.errorhandler(OSError)
def _config_not_writable(exc: OSError):
    """config.toml nicht schreibbar (z. B. gehört auf dem Pi einem anderen
    Benutzer als dem Dienst): verständliche Meldung statt 500. Das Schreiben
    passiert in allen Routen vor der Änderung am Config-Objekt, der laufende
    Stand bleibt also konsistent mit der Datei."""
    log.error("config.toml nicht beschreibbar: %s", exc)
    flash(f"Einstellung nicht gespeichert: {_config_path()} ist nicht beschreibbar "
          f"({exc.strerror or exc}). Dateirechte für den Benutzer des Webdienstes prüfen.",
          "error")
    return redirect(url_for("settings.index"))


@bp.get("")
def index():
    cfg = get_cfg()
    ollama_models = ollama_summarize.list_models(cfg.summary)
    return render_template(
        "settings.html", cfg=cfg, ollama_models=ollama_models,
        prompt_is_default=cfg.summary.prompt_template == SummaryCfg().prompt_template,
        key_in_file=bool(cfg.openrouter.api_key.strip()),
        key_in_env=bool(cfg.openrouter.resolved_api_key()) and not cfg.openrouter.api_key.strip(),
    )


@bp.post("/filters")
def update_filters():
    cfg = get_cfg()
    minutes = request.form.get("min_duration_min", type=int)
    if minutes is None or minutes < 0:
        flash("Ungültige Mindestdauer - bitte eine ganze Zahl ≥ 0 angeben.", "error")
        return redirect(url_for("settings.index"))

    config_writer.update_values(_config_path(), {"filters": {"min_duration_min": minutes}})
    cfg.filters.min_duration_min = minutes
    flash(f"Dauerfilter gespeichert: Videos unter {minutes} Minuten gelten als zu kurz.", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/summary-model")
def update_summary_model():
    cfg = get_cfg()
    model = (request.form.get("model") or "").strip()
    if not model:
        flash("Bitte ein Ollama-Modell angeben.", "error")
        return redirect(url_for("settings.index"))

    config_writer.update_values(_config_path(), {"summary": {"model": model}})
    cfg.summary.model = model
    flash(f"Ollama-Modell für Zusammenfassungen gespeichert: {model}", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/summary-max-chars")
def update_summary_max_chars():
    cfg = get_cfg()
    max_chars = request.form.get("max_chars", type=int)
    if max_chars is None or max_chars < 100:
        flash("Ungültige Zeichenzahl - bitte eine ganze Zahl ≥ 100 angeben.", "error")
        return redirect(url_for("settings.index"))

    config_writer.update_values(_config_path(), {"summary": {"max_chars": max_chars}})
    cfg.summary.max_chars = max_chars
    flash(f"Zielobergrenze gespeichert: {max_chars} Zeichen.", "ok")
    return redirect(url_for("settings.index"))


def _prompt_problem(template: str) -> str | None:
    """Fehlertext, wenn ``template`` nicht als Zusammenfassungs-Prompt taugt -
    ollama_summarize.summarize() ruft ``template.format(text=..., max_chars=...)``
    auf, ein kaputter Prompt würde erst beim nächsten Zusammenfassen auffallen."""
    if "{text}" not in template:
        return ("Der Prompt muss den Platzhalter {text} enthalten - "
                "dort wird das Transkript eingesetzt.")
    try:
        template.format(text="", max_chars=0)
    except (KeyError, IndexError, ValueError):
        return ("Ungültige geschweifte Klammer im Prompt: erlaubt sind nur {text} und "
                "{max_chars}, ein wörtliches { oder } bitte als {{ bzw. }} schreiben.")
    return None


@bp.post("/summary-prompt")
def update_summary_prompt():
    cfg = get_cfg()
    # Browser senden Zeilenumbrüche in Textfeldern als \r\n
    prompt = (request.form.get("prompt") or "").replace("\r\n", "\n").strip()
    problem = _prompt_problem(prompt)
    if problem:
        flash(problem, "error")
        return redirect(url_for("settings.index"))

    config_writer.update_values(_config_path(), {"summary": {"prompt_template": prompt}})
    cfg.summary.prompt_template = prompt
    flash("Prompt für Zusammenfassungen gespeichert - gilt ab der nächsten Zusammenfassung.", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/summary-prompt/reset")
def reset_summary_prompt():
    cfg = get_cfg()
    config_writer.remove_value(_config_path(), "summary", "prompt_template")
    cfg.summary.prompt_template = SummaryCfg().prompt_template
    flash("Prompt auf den Standard zurückgesetzt.", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/openrouter")
def update_openrouter():
    """Modell (Pflicht) und - nur wenn ausgefüllt - API-Schlüssel speichern. Das
    Schlüsselfeld wird nie vorbelegt: leer lassen heißt "unverändert"."""
    cfg = get_cfg()
    model = (request.form.get("model") or "").strip()
    api_key = (request.form.get("api_key") or "").strip()
    if not model:
        flash("Bitte ein OpenRouter-Modell angeben (z. B. anthropic/claude-sonnet-4.5).", "error")
        return redirect(url_for("settings.index"))

    updates: dict[str, object] = {"model": model}
    if api_key:
        updates["api_key"] = api_key
    config_writer.update_values(_config_path(), {"openrouter": updates})
    cfg.openrouter.model = model
    if api_key:
        cfg.openrouter.api_key = api_key
    flash("OpenRouter-Einstellungen gespeichert.", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/openrouter/remove-key")
def remove_openrouter_key():
    cfg = get_cfg()
    config_writer.remove_value(_config_path(), "openrouter", "api_key")
    cfg.openrouter.api_key = ""
    flash("API-Schlüssel aus config.toml entfernt.", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/openrouter/test")
def test_openrouter():
    ok, message = openrouter.check_connection(get_cfg().openrouter)
    flash(message, "ok" if ok else "error")
    return redirect(url_for("settings.index"))
