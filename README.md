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
- Im Caption-Pfad (VTT) gilt dasselbe Prinzip anhand der Cue-Zeitstempel:
  `transcripts.paragraph_pause_s` (Default: `1.8`, `0` deaktiviert das) legt
  fest, ab welcher Lücke zwischen zwei Cues ein neuer Absatz beginnt.
  Wiederholte Cues rollierender Auto-Captions (derselbe Text über mehrere
  Cues, bis die nächste Phrase angehängt wird) zählen dabei nicht als Pause.
- Hat das Video echte YouTube-Kapitel, fügt der Caption-Pfad zusätzlich
  sparsame Überschriften ein (`## 12:34 Kapiteltitel`), gesteuert über
  `transcripts.chapter_headings` (Default: `true`). An einer Kapitelgrenze
  wird kein zusätzlicher Pausen-Absatz mehr gesetzt - die Überschrift trennt
  bereits, damit die Ausgabe nicht mit doppelten Markern überladen wird.
  Ohne Kapitelmetadaten (der Regelfall) entfällt das und es bleibt bei der
  reinen Pausen-Gliederung.

Implementiert in [asr.py](src/ytdigest/sources/asr.py) (`_to_paragraphs`) für
ASR, [vtt.py](src/ytdigest/sources/vtt.py) (`vtt_to_text`) für Captions/Kapitel
und [reflow.py](src/ytdigest/reflow.py) (`wrap_transcript`) für den
Zeilenumbruch je Absatz; aufgerufen in [storage.py](src/ytdigest/storage.py)
beim Ablegen des Transkripts.

## Wartung

yt-dlp regelmäßig aktualisieren, sonst bricht der Abruf still:

```bash
uv lock --upgrade-package yt-dlp && uv sync
```
