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

## Formatierung einzeiliger Transkripte

ASR (faster-whisper) und manche Caption-Spuren mit sehr langen Cues liefern den
Text als einen einzigen, umbruchlosen Absatz. Damit das Transkript lesbar
bleibt, wird es beim Speichern automatisch umgebrochen:

- Ziel ist eine feste Zeilenbreite, konfigurierbar über `line_width` in
  `config.toml` (Default: `120` Zeichen, `0` deaktiviert den Umbruch).
- Es wird nie mitten im Wort getrennt.
- Zeilenenden werden wo möglich an Satzgrenzen (`.`, `!`, `?`) gelegt statt
  mitten im Satz.
- Ist ein einzelner Satz länger als `line_width`, wird er wortweise auf
  mehrere Zeilen umgebrochen.
- Im ASR-Pfad wird zusätzlich anhand von Sprechpausen in Absätze gegliedert:
  liegt zwischen zwei Whisper-Segmenten eine Pause von mindestens
  `paragraph_pause_s` Sekunden (Default: `1.8`, konfigurierbar unter
  `[asr]` in `config.toml`, `0` deaktiviert das), beginnt ein neuer Absatz
  (Leerzeile). Das deutet meist auf einen Themen- oder Sprecherwechsel hin.
  Bei Caption-Spuren entfällt das, da dort keine Segment-Timings verarbeitet
  werden.

Implementiert in [asr.py](src/ytdigest/sources/asr.py) (`_to_paragraphs`,
Absatzbildung) und [reflow.py](src/ytdigest/reflow.py) (`wrap_transcript`,
Zeilenumbruch je Absatz); aufgerufen in [storage.py](src/ytdigest/storage.py)
beim Ablegen des Transkripts.

## Wartung

yt-dlp regelmäßig aktualisieren, sonst bricht der Abruf still:

```bash
uv lock --upgrade-package yt-dlp && uv sync
```
