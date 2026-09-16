# ytdigest

Plattformübergreifendes Python-CLI, das YouTube-RSS-Feeds überwacht, Transkripte
neuer Videos beschafft (vorhandene Untertitel, sonst lokale Spracherkennung via
faster-whisper) und sie dateibasiert in einer kanalbasierten Verzeichnisstruktur
ablegt. Daneben kann `local scan` dieselbe Pipeline auch auf lokal abgelegte
Videodateien anwenden.

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

## Lokale Videodateien

```bash
uv run ytdigest local scan video/                    # scannen + transkribieren
uv run ytdigest local scan video/ --sync-only         # nur einplanen, nicht transkribieren
uv run ytdigest local scan video/ --delete-after-success  # Quelldatei danach löschen
```

Erwartetes Layout: `<verzeichnis>/<kanal>/<datei>.<ext>` - jeder direkte
Unterordner von `<verzeichnis>` ist ein "Kanal" (wird als `channel_id`/`dir_slug`
übernommen, landet also im selben Output-Layout wie YouTube-Kanäle), Videodateien
liegen direkt darin (eine Ebene, keine weitere Verschachtelung).

- **Identität:** `video_id = "<kanal>/<dateiname>"`. Wird eine Datei umbenannt
  oder in einen anderen Kanalordner verschoben, gilt sie als neues Video und
  wird erneut transkribiert.
- **`published_at`:** zuerst die in den Container-Metadaten eingebettete
  `creation_time` (meist das echte Aufnahme-/Exportdatum), sonst die
  Datei-Erstellungszeit. Unter Windows ist das zuverlässig; unter Linux/macOS
  ohne `st_birthtime` ist die Dateisystem-Erstellungszeit nur ein Fallback
  zweiter Wahl (dort eigentlich die letzte Metadatenänderung).
- **Immer ASR:** kein Untertitel-Sidecar-Support (`.srt`/`.vtt` neben der
  Videodatei) - lokale Dateien laufen ausschließlich über faster-whisper,
  direkt auf der Datei (kein Audio-Download-Umweg wie beim YouTube-Pfad).
  Dauer kommt per `ffprobe` (Pflicht im `PATH`, kommt mit `ffmpeg` mit).
- **Anlaufzeit:** eine Datei wird erst aufgenommen, wenn sie seit mindestens
  `local.min_age_s` Sekunden (Default `30`) unverändert ist - Schutz gegen
  das Anfassen einer noch laufenden Kopie. Zu junge Dateien werden beim
  nächsten Scan erneut geprüft.
- **Löschen** passiert nur mit explizitem `--delete-after-success`, und erst
  nachdem das Transkript geschrieben und in der Datenbank als `done` vermerkt
  ist - nie vor gesichertem Erfolg, nie automatisch.
- Berücksichtigte Endungen: `local.extensions` in `config.toml` (Default
  `mp4, mkv, webm, mov, avi, m4v`).

Läuft über dieselbe Datenbank/Zustandsmaschine wie der Feed-Pfad - `status`,
`retry` und `feeds list` funktionieren also auch für lokale Kanäle.
`local scan` verarbeitet dabei ausschließlich die unter `<verzeichnis>`
gefundenen Kanäle, nie anderweitig offen liegende Feed-Videos.

## Podcasts

Neben YouTube-Kanälen kann `feeds.txt` auch normale Podcast-RSS-Feeds
(RSS 2.0 mit `<enclosure>`-Audio-Links, z. B. Buzzsprout, Spotify for
Podcasters, Libsyn) enthalten - Zeile mit `podcast:` präfixen:

```
podcast:https://rss.buzzsprout.com/2402174.rss | Mein Podcast
```

oder per CLI:

```bash
uv run ytdigest feeds add "podcast:https://rss.buzzsprout.com/2402174.rss" --name "Mein Podcast"
```

- **Kein Caption-Pfad:** Podcasts haben keine YouTube-Untertitel - jede Folge
  läuft immer über ASR (faster-whisper), das Audio wird dafür einmalig
  heruntergeladen und danach wieder gelöscht.
- **Dauer:** wird, falls im Feed vorhanden, aus `<itunes:duration>` gelesen -
  dafür ist kein zusätzlicher Download nötig. Fehlt das Tag, wird die Folge
  ungefiltert verarbeitet und die echte Dauer erst beim Transkribieren
  bekannt.
- **Identität:** Episoden werden über ihre `<guid>` (Fallback: die
  Enclosure-URL) erkannt, nicht über den Titel - ein geändertes Postdatum
  oder eine Titelkorrektur führt also nicht zu einer erneuten Transkription.
- Läuft über dieselbe Datenbank/Zustandsmaschine wie der YouTube-Pfad -
  `status`, `retry`, `feeds list` und `run --feed <id>` funktionieren also
  auch für Podcast-Kanäle.

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
