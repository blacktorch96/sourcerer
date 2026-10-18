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
  `mp4, mkv, webm, mov, avi, m4v, mp3, wav, m4a`).

Läuft über dieselbe Datenbank/Zustandsmaschine wie der Feed-Pfad - `status`,
`retry` und `feeds list` funktionieren also auch für lokale Kanäle.
`local scan` verarbeitet dabei ausschließlich die unter `<verzeichnis>`
gefundenen Kanäle, nie anderweitig offen liegende Feed-Videos.

### Einzelner Ordner als Feed (`local:` in feeds.txt)

Für einen einzelnen Ordner (keine Kanal-Unterordner, Dateien liegen direkt
darin) reicht ein Eintrag in `feeds.txt` - er wird dann bei jedem normalen
`run`/Sync mitberücksichtigt, ohne extra `local scan`:

```
local:/home/me/aufnahmen | Meine Aufnahmen
local:C:\Users\me\Aufnahmen
```

- Funktioniert mit Windows- (`C:\...`) und Linux/macOS-Pfaden (`/...`),
  unabhängig vom Betriebssystem, auf dem `feeds.txt` gerade gelesen wird -
  praktisch für eine über mehrere Geräte synchronisierte `feeds.txt`
  (ein Pfad, der auf dem aktuellen Gerät nicht existiert, wird beim Sync
  einfach übersprungen, siehe `feeds_failed`).
- **Vorhandenes Transkript übernehmen:** liegt neben einer Mediendatei
  bereits eine gleichnamige `.txt` (z. B. `episode1.mp3` + `episode1.txt`),
  wird deren Inhalt direkt als Transkript übernommen - kein ASR-Lauf, keine
  Dauer-Filterung. Fehlt die `.txt`, läuft die Datei wie gewohnt über ASR.
- Entfällt die `local:`-Zeile aus `feeds.txt`, wird der Feed wie jeder
  andere auch deaktiviert (`is_active = 0`), nicht gelöscht.

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

## Web-Oberfläche

```bash
uv sync --extra web       # Flask installieren
uv run ytdigest web serve                    # http://127.0.0.1:5050
uv run ytdigest web serve --host 0.0.0.0 --port 8080  # im LAN erreichbar
```

Rein lokales Werkzeug ohne Login - Bind-Adresse deshalb per Default `127.0.0.1`
(überschreibbar per `--host`/`--port` oder `[web]` in `config.toml`).

- **Dashboard** (`/`): Zustände wie `ytdigest status`, letzte Fehler, globaler
  "Sync & Verarbeitung jetzt starten"-Button mit Live-Log.
- **Feeds** (`/feeds`): Feeds hinzufügen/entfernen (schreibt weiterhin
  `feeds.txt` - das bleibt die Quelle der Wahrheit), sowie Sync/Backfill/Retry
  je Kanal.
- **Transkripte** (`/transcripts`): nach Kanal filtern, im Browser lesen,
  `.txt`/`.json` herunterladen.

Ausgelöste Läufe (Sync, Backfill) laufen im selben Prozess in einem
Hintergrund-Thread und nutzen denselben Single-Instance-Lock wie die CLI -
ein Web-Trigger kollidiert also nicht mit einem parallel laufenden
Cron-Aufruf, sondern bekommt stattdessen eine "Lock gehalten"-Meldung im
Live-Log. Es läuft immer nur ein Job gleichzeitig, unabhängig davon, wie
viele Browser-Tabs offen sind.

### Zusammenfassung (experimentell)

Auf jeder Transkript-Detailseite lässt sich per Button eine Zusammenfassung
über ein lokal laufendes [Ollama](https://ollama.com) erzeugen. Bewusst
isoliert vom Rest des Tools gehalten (eigene Dateien, kein Eingriff in die
Sync-/ASR-Pipeline oder das Datenmodell), weil das Feature noch getestet
wird und sich je nach Ergebnis wieder komplett entfernen lässt:

- `src/ytdigest/sources/ollama_summarize.py` - der eigentliche Ollama-Aufruf
- `src/ytdigest/web/summary_views.py` - Route + Ablagelogik, mit
  Entfernungsanleitung im Datei-Kopf
- Konfiguration unter `[summary]` in `config.toml` (Modell, Prompt,
  Zeichenobergrenzen, Ollama-Host) - siehe Kommentare in `config.py`

Ablage als `<transkript-basename>.summary.md` neben dem Transkript, ein
fehlgeschlagener Versuch als `.summary.error.txt` (wird auf der Detailseite
mit "Erneut versuchen"-Button angezeigt, bis ein Versuch klappt).

```bash
ollama pull qwen3.5:9b     # oder ein anderes lokal installiertes Modell
```

## Profile

Mehrere unabhängige Recherchebereiche (eigene Feeds, Datenbank, Transkripte,
ASR-/Zusammenfassungs-Einstellungen) nebeneinander, z. B. "technologie" und
"studium". Ein Profil ist einfach eine weitere vollständige `config.toml`
unter `profiles/<name>/config.toml` - Pipeline und Web-Oberfläche kennen
keinen eigenen Profil-Begriff, sie bekommen nur jeweils "die aktuelle Config"
gereicht.

```bash
uv run ytdigest profiles list                  # gefundene Profile anzeigen
uv run ytdigest --profile studium run           # Kurzform für --config profiles/studium/config.toml
uv run ytdigest --profile studium web serve     # eigene Web-Oberfläche für dieses Profil
```

Ohne `--profile`/`--config` verhält sich das Tool unverändert wie bisher
(normale `./config.toml`-Auflösung). In der Web-Oberfläche erscheint oben
rechts ein Dropdown, sobald mindestens ein Profil existiert - ein Wechsel
dort ändert Dashboard/Feeds/Transkripte sofort auf das gewählte Profil, ohne
Serverneustart (nicht möglich, während gerade ein Job läuft).

Ein neues Profil anlegen: Ordner `profiles/<name>/` mit eigener `config.toml`
(`[paths]` auf `profiles/<name>/...` zeigen lassen) erstellen, dann einmalig
`ytdigest --profile <name> init`.

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
uv lock --upgrade-package yt-dlp && uv sync --extra asr
```

`--extra asr` beim `uv sync` nicht vergessen (bzw. an die eigene Installation
anpassen, siehe [Installation](#installation)) - ein bloßes `uv sync` synct
nur auf die explizit angegebenen Extras und deinstalliert sonst
`faster-whisper` wieder, auch wenn es vorher installiert war.
