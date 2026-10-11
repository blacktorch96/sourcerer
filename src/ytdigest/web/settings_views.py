"""Einstellungsseite: Dauerfilter (filters.min_duration_min) und
Ollama-Modell für die Zusammenfassung (summary.model).

Schreibt gezielt in config.toml (config_writer.py, kommentarerhaltend) und
aktualisiert zusätzlich das im Prozess gehaltene Config-Objekt direkt, damit
eine Änderung sofort wirkt statt erst nach einem Neustart von
'ytdigest web serve'.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ytdigest import config_writer
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
    return render_template("settings.html", cfg=cfg, ollama_models=ollama_models)


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
