# ytdigest

Plattformübergreifendes Python-CLI, das YouTube-RSS-Feeds überwacht, Transkripte
neuer Videos beschafft (vorhandene Untertitel, sonst lokale Spracherkennung via
faster-whisper) und sie dateibasiert in einer kanalbasierten Verzeichnisstruktur
ablegt.

Details in [spec.md](spec.md).

## Installation

```bash
uv sync --extra asr      # mit ASR-Fallback
uv sync                  # nur Caption-Pfad (ohne GPU-Abhängigkeiten)
```

Der ASR-Pfad braucht zusätzlich `ffmpeg` im `PATH`.

## Nutzung

```bash
uv run ytdigest init                  # Datenbank und Verzeichnisse anlegen
uv run ytdigest feeds add @t3dotgg    # Feed hinzufügen
uv run ytdigest run --limit 20        # Sync + Verarbeitung
uv run ytdigest status                # Zählwerte je Zustand
uv run ytdigest retry                 # fehlgeschlagene Videos neu einplanen
```

`run` eignet sich für Cron / Task Scheduler. Exit-Code 0 = sauber, 1 = mindestens
ein Video fehlgeschlagen, 2 = Startfehler, 3 = anderer Lauf hält das Lock.

## Wartung

yt-dlp regelmäßig aktualisieren, sonst bricht der Abruf still:

```bash
uv lock --upgrade-package yt-dlp && uv sync
```
