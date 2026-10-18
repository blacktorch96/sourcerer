"""Umschalten zwischen Profilen (siehe profiles.py). Ändert die im
Prozess gehaltene Config für Dashboard/Feeds/Transkripte/Jobs sofort, ohne
Serverneustart - app.config['YTDIGEST_CFG'] und JobRunner.cfg werden beide
auf das neu geladene Config-Objekt umgehängt.
"""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, request, url_for

from ytdigest.config import load_config
from ytdigest.profiles import list_profiles, profile_config_path
from ytdigest.web.context import get_jobs

bp = Blueprint("profiles", __name__, url_prefix="/profile")


@bp.post("/switch")
def switch():
    jobs = get_jobs()
    if jobs.running:
        flash("Erst den laufenden Job abwarten, bevor du das Profil wechselst.", "error")
        return redirect(request.referrer or url_for("dashboard.index"))

    name = (request.form.get("profile") or "").strip()
    if not name or name not in list_profiles():
        flash(f"Unbekanntes Profil: {name or '(leer)'}", "error")
        return redirect(request.referrer or url_for("dashboard.index"))

    try:
        new_cfg = load_config(profile_config_path(name))
    except Exception as exc:  # noqa: BLE001 - defekte profiles/<name>/config.toml darf die UI nicht zerlegen
        flash(f"Profil '{name}' konnte nicht geladen werden: {exc}", "error")
        return redirect(request.referrer or url_for("dashboard.index"))

    current_app.config["YTDIGEST_CFG"] = new_cfg
    jobs.cfg = new_cfg
    flash(f"Profil gewechselt: {name}", "ok")
    return redirect(url_for("dashboard.index"))
