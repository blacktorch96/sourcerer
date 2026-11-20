"""Gezieltes, kommentarerhaltendes Schreiben einzelner config.toml-Werte -
für die Web-Einstellungsseite (settings_views.py). Eine Neugenerierung der
ganzen Datei aus dem Config-Objekt würde Kommentare und die Reihenfolge
zerstören, die beim Lesen genutzte ``tomllib`` kann zudem nicht schreiben -
deshalb ``tomlkit`` statt eines eigenen Nur-dieses-Feld-Patchens per Regex.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit


def update_values(path: Path, updates: dict[str, dict[str, object]]) -> None:
    """Setzt einzelne Werte in ``path`` (z. B. ``{"filters": {"min_duration_min": 8}}``),
    alles andere in der Datei bleibt unverändert. Legt Datei/Abschnitte an,
    falls sie noch nicht existieren."""
    if path.is_file():
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = tomlkit.document()

    for section, values in updates.items():
        if section not in doc:
            doc[section] = tomlkit.table()
        for key, value in values.items():
            if isinstance(value, str) and "\n" in value:
                # mehrzeilige Texte (z. B. Prompts) lesbar als """...""" statt
                # als eine Zeile voller \n
                value = tomlkit.string(value, multiline=True)
            doc[section][key] = value

    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def remove_value(path: Path, section: str, key: str) -> None:
    """Entfernt einen Schlüssel wieder aus ``path`` (Rückkehr zum Default, der
    dann nicht in der Datei eingefroren ist). Fehlende Datei/Abschnitt/Schlüssel
    sind kein Fehler."""
    if not path.is_file():
        return
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    if section in doc and key in doc[section]:
        del doc[section][key]
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")
