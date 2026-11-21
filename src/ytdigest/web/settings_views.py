"""Einstellungsseite: Dauerfilter (filters.min_duration_min) sowie Ollama-Modell
(summary.model) und Prompt (summary.prompt_template) für die Zusammenfassung.

Schreibt gezielt in config.toml (config_writer.py, kommentarerhaltend) und
aktualisiert zusätzlich das im Prozess gehaltene Config-Objekt direkt, damit
eine Änderung sofort wirkt statt erst nach einem Neustart von
'ytdigest web serve'.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ytdigest import config_writer
from ytdigest.config import SummaryCfg
from ytdigest.sources import ollama_summarize
from ytdigest.web.context import get_cfg

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _config_path() -> Path:
    cfg = get_cfg()
    return cfg.source_path or Path("config.toml")


@bp.get("")
def index():
    cfg = get_cfg()
    ollama_models = ollama_summarize.list_models(cfg.summary)
    return render_template(
        "settings.html", cfg=cfg, ollama_models=ollama_models,
        prompt_is_default=cfg.summary.prompt_template == SummaryCfg().prompt_template,
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
