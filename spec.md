# spec.md — ytdigest

Spezifikation für ein plattformübergreifendes Python-CLI, das YouTube-RSS-Feeds
überwacht, Transkripte neuer Videos beschafft und sie dateibasiert ablegt.

- **Version:** 0.1 (Entwurf)
- **Stand:** 2026-09-03
- **Zielplattformen:** Entwicklung Windows 11, Betrieb Linux (headless)

---

## 1. Zweck und Zielbild

Das Tool baut ein lokales, wachsendes Textkorpus aus YouTube-Inhalten auf. Es läuft
unbeaufsichtigt (Cron bzw. Task Scheduler), erkennt neue Videos in hinterlegten
RSS-Feeds und legt für jedes Video eine Transkriptdatei plus Metadatendatei ab.

Die Zusammenfassung durch ein LLM ist **nicht** Teil dieser Version, das Datenmodell
und das Ablageformat sind aber so entworfen, dass ein Folgeschritt ohne Umbau
andocken kann.

---

## 2. Scope

**In Scope**

- Verwaltung einer Feed-Liste in einer einfachen Textdatei
- Abruf und Parsen der YouTube-Atom-Feeds
- Deduplizierung über eine SQLite-Datenbank
- Transkriptbeschaffung über vorhandene Untertitel
- Fallback auf lokale Spracherkennung (faster-whisper), wenn keine Untertitel existieren
- Ablage als `.txt` plus `.json`-Sidecar in einer kanalbasierten Verzeichnisstruktur
- Wiederaufnahme nach Abbruch, Retry fehlgeschlagener Videos

**Out of Scope (diese Version)**

- LLM-Zusammenfassung, Embedding, Suche
- Web-UI oder API
- Mehrbenutzerbetrieb, Authentifizierung
- Download der Videodateien selbst (Audio wird nur temporär für ASR gezogen)
- Playlists, Suchergebnisse, Live-Streams als Quelle

---

## 3. Systemüberblick

```
feeds.txt ──> Feed-Reader ──> SQLite (videos.status) ──> Worker
                                                           │
                                    ┌──────────────────────┤
                                    │                      │
                             Captions (yt-dlp)      ASR (faster-whisper)
                                    │                      │
                                    └──────────┬───────────┘
                                               │
                                          Storage-Layer
                                               │
                        transkripte/{kanal}/{datum}_{titel}.txt + .json
```

Ein Lauf besteht aus drei Phasen:

1. **Sync:** `feeds.txt` gegen die DB abgleichen, Feeds abrufen, neue Videos als
   `discovered` anlegen.
2. **Process:** alle Videos im Zustand `discovered` bzw. retry-fähigen Zuständen
   abarbeiten.
3. **Report:** Zusammenfassung auf der Konsole, Exit-Code setzen.

Phase 1 und 2 sind getrennt, damit ein Abbruch während der ASR nicht dazu führt,
dass Videos verloren gehen. Was einmal in der DB steht, wird beim nächsten Lauf
wieder aufgegriffen.

---

## 4. Konfiguration

### 4.1 Feed-Liste (`feeds.txt`)

UTF-8, eine Quelle pro Zeile. Leerzeilen und Zeilen mit `#` werden ignoriert.
Optional lässt sich hinter einem Pipe-Zeichen ein Anzeigename erzwingen, der den
Kanaltitel aus dem Feed überschreibt (nützlich, wenn Kanäle sich umbenennen und
die Verzeichnisstruktur stabil bleiben soll).

```
# Web-Entwicklung
https://www.youtube.com/feeds/videos.xml?channel_id=UCbRP3c757lWg9M-U7TyEkXA
@t3dotgg | Theo
UCbRP3c757lWg9M-U7TyEkXA
```

Akzeptierte Formen je Zeile:

| Form | Verhalten |
|------|-----------|
| Vollständige `videos.xml`-URL | direkt übernehmen |
| Kanal-ID (`UC…`, 24 Zeichen) | zur Feed-URL expandieren |
| `@handle` oder Kanal-URL | einmalig zur Kanal-ID auflösen, Ergebnis in DB cachen |

Die Auflösung von `@handle` zur `UC`-ID erfolgt genau einmal pro Kanal und wird in
`feeds.channel_id` persistiert. Bei jedem Lauf erneut aufzulösen wäre unnötiger
Traffic und eine zusätzliche Fehlerquelle.

### 4.2 `config.toml`

Suchreihenfolge: `--config`-Pfad, dann `./config.toml`, dann Benutzer-Config-Verzeichnis
(via `platformdirs`). Fehlt die Datei, gelten die Defaults.

```toml
[paths]
feeds_file   = "feeds.txt"
database     = "data/ytdigest.sqlite3"
output_dir   = "transkripte"
temp_dir     = ""            # leer = Systemtemp
log_file     = "logs/ytdigest.log"

[feeds]
request_timeout_s   = 15
delay_between_s     = 2.0    # Höflichkeitspause zwischen Feed-Abrufen
use_conditional_get = true   # ETag / If-Modified-Since

[transcripts]
languages        = ["de", "en"]   # Präferenzreihenfolge
prefer_manual    = true           # manuelle vor Auto-Captions
max_attempts     = 3
retry_backoff_s  = [60, 300, 1800]

[filters]
min_duration_min = 8              # kürzere Videos überspringen (Shorts, Clips)

[asr]
enabled          = true
model            = "large-v3"
device           = "cuda"         # "cuda" | "cpu" | "auto"
compute_type     = "float16"
beam_size        = 5
max_duration_min = 90             # längere Videos nicht per ASR verarbeiten

[output]
include_video_id  = false   # true = Video-ID immer an den Dateinamen hängen
max_filename_len  = 120
timestamp_source  = "published_utc"
```

Jede Option ist zusätzlich per Umgebungsvariable mit Präfix `YTDIGEST_`
überschreibbar (`YTDIGEST_ASR__DEVICE=cpu`). Das erleichtert den Betrieb, wenn
Windows-Entwicklungsrechner und Linux-Server unterschiedliche Hardware haben.

---

## 5. Datenmodell (SQLite)

Zugriff über `sqlite3` aus der Standardbibliothek, kein ORM. Der Umfang
rechtfertigt SQLAlchemy nicht. WAL-Modus aktiv, `foreign_keys = ON`.

```sql
CREATE TABLE schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL
);

CREATE TABLE feeds (
    id              INTEGER PRIMARY KEY,
    channel_id      TEXT    NOT NULL UNIQUE,
    feed_url        TEXT    NOT NULL UNIQUE,
    channel_title   TEXT,
    display_name    TEXT,              -- Override aus feeds.txt
    dir_slug        TEXT    NOT NULL,  -- eingefrorener Verzeichnisname
    etag            TEXT,
    last_modified   TEXT,
    added_at        TEXT    NOT NULL,
    last_checked_at TEXT,
    last_error      TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE videos (
    video_id        TEXT    PRIMARY KEY,
    feed_id         INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    title           TEXT    NOT NULL,
    published_at    TEXT    NOT NULL,   -- ISO 8601, UTC, mit Z
    url             TEXT    NOT NULL,
    duration_s      INTEGER,
    status          TEXT    NOT NULL,
    source          TEXT,               -- captions_manual | captions_auto | asr
    language        TEXT,
    transcript_path TEXT,
    metadata_path   TEXT,
    char_count      INTEGER,
    word_count      INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    skip_reason     TEXT,               -- too_short | initial_sync | filtered
    next_retry_at   TEXT,
    first_seen_at   TEXT    NOT NULL,
    processed_at    TEXT,
    summary_status  TEXT    NOT NULL DEFAULT 'none'
);

CREATE INDEX idx_videos_status    ON videos(status, next_retry_at);
CREATE INDEX idx_videos_feed      ON videos(feed_id, published_at DESC);
CREATE INDEX idx_videos_summary   ON videos(summary_status);
```

`dir_slug` wird beim ersten Anlegen des Kanals berechnet und danach nicht mehr
geändert. Sonst entstehen bei einer Kanalumbenennung zwei Verzeichnisse mit
demselben Inhalt.

`summary_status` ist bereits vorgesehen, wird in dieser Version aber nur auf
`none` gesetzt. Der spätere Summarizer setzt hier `pending` und `done`.

### 5.1 Zustandsautomat `videos.status`

```
discovered ──> processing ──> done
     │              │
     │              ├──> no_transcript   (keine Captions, ASR aus oder nicht möglich)
     │              └──> failed          (Fehler, attempts < max → retry-fähig)
     │
     └──> skipped   (--initial-mode mark-seen, oder Filter greift)
```

| Zustand | Bedeutung | Beim nächsten Lauf |
|---------|-----------|--------------------|
| `discovered` | erkannt, noch nicht verarbeitet | wird verarbeitet |
| `processing` | Verarbeitung läuft | bei Neustart auf `discovered` zurückgesetzt |
| `done` | Transkript liegt vor | ignoriert |
| `no_transcript` | endgültig ohne Text | nur mit `retry --no-transcript` |
| `failed` | Fehler aufgetreten | wenn `attempts < max_attempts` und `next_retry_at` erreicht |
| `skipped` | bewusst übersprungen, Grund in `skip_reason` | ignoriert |

Der Reset von `processing` auf `discovered` beim Start ist notwendig, weil ein
harter Abbruch während eines ASR-Laufs sonst Videos dauerhaft blockieren würde.

---

## 6. Verarbeitungspipeline

### 6.1 Feed-Sync

1. `feeds.txt` einlesen, Einträge normalisieren, mit `feeds`-Tabelle abgleichen.
   Neue Einträge werden angelegt, entfernte Einträge auf `is_active = 0` gesetzt
   (nicht gelöscht, sonst gehen die Video-Historien verloren).
2. Je Feed einen HTTP-GET mit `If-None-Match` bzw. `If-Modified-Since`. Bei `304`
   wird der Feed übersprungen.
3. Atom-Antwort parsen. Relevante Felder: `yt:videoId`, `title`, `published`,
   `link[@rel=alternate]`, `yt:channelId`, `media:group/media:title`.
4. Videos, die bereits in `videos` stehen, überspringen. Neue Videos anlegen mit
   `status = discovered` oder `skipped`, je nach Erstlauf-Modus.
5. Pause gemäß `feeds.delay_between_s` vor dem nächsten Feed.

### 6.2 Erstlauf-Verhalten

Der Modus ist per CLI wählbar und gilt nur für Feeds, die zum ersten Mal
abgerufen werden (also `feeds.last_checked_at IS NULL`):

| Flag | Verhalten |
|------|-----------|
| `--initial-mode latest` | nur das neueste geeignete Video verarbeiten, Rest `skipped` (Default) |
| `--initial-mode backfill` | alle Videos im Feed (max. 15) werden verarbeitet |
| `--initial-mode mark-seen` | alle als `skipped` markieren, erst ab jetzt neue Videos |

`latest` bedeutet nicht schlicht "das oberste Element im Feed". Die Videodauer
steht nicht im RSS, sie kommt erst mit dem yt-dlp-Metadatenabruf. Der Modus geht
deshalb die Feed-Einträge von neu nach alt durch, holt je Kandidat die Metadaten
und nimmt den ersten, der den Dauerfilter besteht. Alle übersprungenen Kandidaten
bekommen `status = skipped` mit `skip_reason = too_short`, alle übrigen
`skip_reason = initial_sync`.

Ohne diesen Durchlauf würde bei einem Kanal, dessen letzter Upload ein Short ist,
beim Anlegen des Feeds gar nichts transkribiert. Die Obergrenze liegt bei fünf
geprüften Kandidaten, danach gilt der Feed als anfangs leer.

`backfill` bleibt bewusst nicht der Default. Wer 40 Feeds auf einmal hinzufügt,
löst damit bis zu 600 Videos aus, davon potenziell viele über den ASR-Pfad.

### 6.3 Video-Verarbeitung

Pro Video, sequenziell:

1. Status auf `processing`, `attempts += 1`.
2. Metadaten via yt-dlp abrufen (Dauer, verfügbare Untertitelspuren). Wenn das
   Video privat, gelöscht oder geoblockt ist: `failed` mit Klartextfehler.
3. **Dauerfilter:** liegt `duration_s` unter `filters.min_duration_min`, wird das
   Video auf `skipped` mit `skip_reason = too_short` gesetzt und der Rest der
   Verarbeitung entfällt. Die Dauer wird trotzdem in die DB geschrieben, damit
   eine spätere Senkung der Schwelle diese Videos ohne erneuten Abruf findet.
4. **Caption-Pfad:** manuelle Untertitel in Sprachpräferenzreihenfolge, danach
   Auto-Captions. Erfolgt ein Treffer, VTT holen und in Fließtext wandeln.
5. **ASR-Pfad** (nur wenn Schritt 4 leer bleibt und `asr.enabled`):
   Dauer gegen `asr.max_duration_min` prüfen, Audio als temporäre Datei ziehen,
   faster-whisper laufen lassen, Temp-Datei anschließend in jedem Fall löschen.
6. Ergebnis über den Storage-Layer schreiben.
7. Status auf `done`, Pfade und Zählwerte in die DB.

Fehler in Schritt 2 bis 6 führen zu `failed` mit gesetztem `next_retry_at` gemäß
`retry_backoff_s`. Bei `attempts >= max_attempts` bleibt der Zustand `failed`,
wird aber nicht mehr automatisch aufgegriffen.

### 6.4 VTT-Normalisierung

Auto-Captions enthalten überlappende Cues, Inline-Tags und wiederholte Zeilen.
Die Normalisierung entfernt:

- `WEBVTT`-Header sowie `Kind:` und `Language:`-Zeilen
- Zeitstempelzeilen und Cue-Nummern
- Inline-Tags der Form `<00:00:01.234>` und `<c>…</c>`
- direkt aufeinanderfolgende identische Zeilen

Ergebnis ist ein Fließtext ohne Zeitmarken. Zeitmarken werden bewusst verworfen,
weil das Ziel die LLM-Zusammenfassung ist und Timestamps dort nur Tokens kosten.
Wer sie später braucht, kann das Original-VTT über die Video-ID neu ziehen.

---

## 7. Ausgabeformat

### 7.1 Verzeichnis- und Dateinamen

```
transkripte/
  Theo/
    2026-08-30_143012_Why_Your_Types_Are_Lying_To_You.txt
    2026-08-30_143012_Why_Your_Types_Are_Lying_To_You.json
```

- Verzeichnis: `output_dir / <feeds.dir_slug>`
- Basisname: `{published_at:%Y-%m-%d_%H%M%S}_{title_slug}`
- Zeitstempel in UTC, Quelle ist `published_at` aus dem Feed. Lokale Zeit wäre
  auf Windows-Entwicklungsrechner und Linux-Server unterschiedlich und würde zu
  abweichenden Dateinamen für dasselbe Video führen.

### 7.2 Slug-Regeln (plattformübergreifend)

Der Slug muss auf NTFS und ext4 gleichermaßen gültig sein, also gilt jeweils die
strengere Regel:

1. Unicode-Normalisierung NFKD, deutsche Umlaute transliteriert (`ä` → `ae`,
   `ö` → `oe`, `ü` → `ue`, `ß` → `ss`)
2. Entfernen von `< > : " / \ | ? *` sowie allen Steuerzeichen
3. Whitespace-Folgen zu einem `_` zusammenfassen, mehrfache `_` reduzieren
4. Führende und abschließende Punkte, Leerzeichen und Unterstriche entfernen
5. Reservierte Windows-Namen (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`,
   `LPT1`–`LPT9`) mit `_` suffixen
6. Kürzen auf `output.max_filename_len` Zeichen, an einer Wortgrenze
7. Ist das Ergebnis leer, wird die Video-ID als Slug verwendet

Kollisionen (gleiche Sekunde, gleicher Titel) werden durch Anhängen von
`_{video_id}` aufgelöst. Mit `output.include_video_id = true` passiert das immer.

### 7.3 Transkriptdatei (`.txt`)

Reiner Text, UTF-8, LF als Zeilenende (explizit `newline="\n"`, damit Windows
keine CRLF einfügt). Kein Header, keine Zeitmarken. Absätze werden durch
Leerzeilen getrennt, wenn die Quelle das hergibt, sonst ist es ein Block.

### 7.4 Metadatendatei (`.json`)

```json
{
  "schema_version": 1,
  "video_id": "dQw4w9WgXcQ",
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "title": "Why Your Types Are Lying To You",
  "channel": {
    "id": "UCbRP3c757lWg9M-U7TyEkXA",
    "title": "Theo - t3.gg",
    "dir_slug": "Theo"
  },
  "published_at": "2026-08-30T14:30:12Z",
  "duration_s": 1284,
  "transcript": {
    "source": "captions_auto",
    "language": "en",
    "asr_model": null,
    "char_count": 18342,
    "word_count": 3120,
    "file": "2026-08-30_143012_Why_Your_Types_Are_Lying_To_You.txt"
  },
  "fetched_at": "2026-09-03T06:12:44Z",
  "tool_version": "0.1.0"
}
```

Bei ASR-Herkunft steht in `asr_model` der verwendete Modellname, `source` ist
dann `asr`. Der relative Dateiname statt eines absoluten Pfads hält die Ablage
verschiebbar zwischen Windows und Linux.

### 7.5 Schreibvorgang

Beide Dateien werden zuerst als `*.tmp` im Zielverzeichnis geschrieben und dann
per `os.replace()` atomar umbenannt. Erst wenn beide Dateien liegen, wird die DB
auf `done` gesetzt. Damit gibt es keinen Zustand, in dem die DB ein Transkript
behauptet, das auf der Platte fehlt.

---

## 8. CLI

Framework: Typer. Alle Kommandos akzeptieren `--config`, `--verbose`, `--quiet`.

```
ytdigest init                       Datenbank und Verzeichnisse anlegen
ytdigest feeds sync                 feeds.txt in die DB übernehmen
ytdigest feeds list                 Feeds mit Videoanzahl und letztem Abruf
ytdigest feeds add URL|@handle      Zeile an feeds.txt anhängen und syncen
ytdigest run [OPTIONS]              Sync und Verarbeitung
ytdigest status                     Zählwerte je Zustand, letzte Fehler
ytdigest retry [OPTIONS]            fehlgeschlagene Videos neu einplanen
ytdigest paths                      aufgelöste Pfade ausgeben (Debugging)
```

### 8.1 Optionen für `run`

| Option | Default | Wirkung |
|--------|---------|---------|
| `--initial-mode {latest,backfill,mark-seen}` | `latest` | Verhalten bei neuen Feeds |
| `--min-duration MIN` | aus Config (8) | Dauerfilter für diesen Lauf überschreiben |
| `--limit N` | unbegrenzt | max. Anzahl verarbeiteter Videos pro Lauf |
| `--feed CHANNEL_ID` | alle | auf einzelne Feeds einschränken, mehrfach nutzbar |
| `--since YYYY-MM-DD` | keine | nur Videos ab diesem Datum |
| `--no-asr` | aus | ASR-Fallback für diesen Lauf deaktivieren |
| `--asr-only` | aus | nur Videos im Zustand `no_transcript` per ASR nachholen |
| `--sync-only` | aus | nur Feeds abrufen, nichts verarbeiten |
| `--dry-run` | aus | zeigt, was passieren würde, schreibt nichts |

`--limit` in Kombination mit einem Cron-Intervall ist der praktische Weg, einen
großen Backfill über mehrere Läufe zu verteilen, ohne YouTube zu belasten.

### 8.2 Exit-Codes

| Code | Bedeutung |
|------|-----------|
| 0 | alles verarbeitet oder nichts zu tun |
| 1 | Lauf beendet, aber mindestens ein Video fehlgeschlagen |
| 2 | Konfigurations- oder Startfehler |
| 3 | anderer Lauf hält bereits das Lock |
| 130 | durch Benutzer abgebrochen |

Die Trennung von 0 und 1 ist wichtig, damit Monitoring auf dem Server zwischen
"lief sauber" und "lief, aber mit Problemen" unterscheiden kann.

---

## 9. Plattform-Kompatibilität

| Thema | Umsetzung |
|-------|-----------|
| Pfade | ausschließlich `pathlib.Path`, keine String-Konkatenation |
| Pfadlängen | Dateiname auf 120 Zeichen begrenzt, Basisverzeichnis kurz halten (Windows-MAX_PATH) |
| Dateinamen | Slug-Regeln nach Abschnitt 7.2, strengste gemeinsame Teilmenge |
| Zeilenenden | Schreiben immer mit `encoding="utf-8", newline="\n"` |
| Konsole | UTF-8 erzwingen, damit Titel mit Sonderzeichen unter Windows nicht crashen |
| Zeitzone | intern durchgängig UTC, `datetime.now(timezone.utc)` |
| Single-Instance | Lockfile über `os.open(..., O_CREAT \| O_EXCL)`, enthält PID und Startzeit; als stale verworfen, wenn älter als 12 h |
| ffmpeg | nur für den ASR-Pfad nötig, beim Start via `shutil.which` prüfen und mit klarer Meldung abbrechen |
| GPU | `asr.device = "auto"` fällt auf CPU zurück, wenn keine CUDA-Runtime da ist |

Zum ASR-Pfad: die faster-whisper-Abhängigkeit hängt an CTranslate2 und damit an
einer passenden CUDA-Version. Das ist der fragilste Teil der Installation. Deshalb
sitzt ASR in einem optionalen Dependency-Extra, sodass der Caption-Pfad auf einem
Rechner ohne GPU vollständig ohne diese Abhängigkeiten installierbar bleibt.

---

## 10. Fehlerbehandlung und Logging

- Jeder Videofehler wird abgefangen, protokolliert und in `videos.last_error`
  abgelegt. Ein einzelnes fehlerhaftes Video darf den Lauf nie beenden.
- HTTP 429 und 403 vom Feed-Abruf oder von yt-dlp führen zu Backoff und Abbruch
  des laufenden Feeds, nicht des gesamten Laufs.
- yt-dlp-Fehlermeldungen werden auf eine kurze Klartextursache reduziert
  (`private`, `unavailable`, `age_restricted`, `rate_limited`, `unknown`), damit
  `ytdigest status` auswertbar bleibt.
- Logging über `logging` mit zwei Handlern: `RichHandler` auf der Konsole und
  `RotatingFileHandler` auf `paths.log_file` (5 MB, 5 Backups).
- Beim Beenden eine Zusammenfassung: geprüfte Feeds, neue Videos, verarbeitet,
  per Captions, per ASR, fehlgeschlagen, Laufzeit.

---

## 11. Nichtfunktionale Anforderungen

- **Erwartete Last:** bis 50 Feeds, Laufintervall 30 bis 60 Minuten
- **Laufzeit Caption-Pfad:** wenige Sekunden pro Video
- **Laufzeit ASR-Pfad:** dominierender Faktor, deshalb `--limit` und
  `max_duration_min` als Schutz
- **Nebenläufigkeit:** bewusst keine. Feed-Abrufe sind schnell genug, und der
  ASR-Schritt ist GPU-gebunden und damit ohnehin seriell.
- **Wiederanlauf:** jeder Lauf ist idempotent, ein Abbruch zu jedem Zeitpunkt
  darf keinen Datenverlust und keine Duplikate erzeugen
- **Rechtliches:** Transkripte sind urheberrechtlich geschütztes Material. Die
  Ablage ist für den persönlichen Gebrauch gedacht, keine Weiterveröffentlichung.

---

## 12. Projektstruktur

Modulschnitt so gewählt, dass jede Datei unter 150 Zeilen bleibt.

```
ytdigest/
├── pyproject.toml
├── config.toml
├── feeds.txt
├── README.md
├── src/ytdigest/
│   ├── __init__.py
│   ├── cli.py              ~130  Typer-App, Kommandos delegieren nur
│   ├── config.py           ~90   Laden, Validieren, Defaults, ENV-Override
│   ├── models.py           ~80   Dataclasses: Feed, Video, TranscriptResult
│   ├── db/
│   │   ├── __init__.py
│   │   ├── schema.py       ~90   DDL und Migrationen
│   │   └── repo.py         ~140  Queries für Feeds und Videos
│   ├── feeds/
│   │   ├── __init__.py
│   │   ├── parser.py       ~90   feeds.txt lesen, Zeilen normalisieren
│   │   └── fetcher.py      ~120  HTTP mit Conditional GET, Atom parsen
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── captions.py     ~120  yt-dlp Untertitel, Sprachauswahl
│   │   ├── vtt.py          ~80   VTT nach Fließtext
│   │   └── asr.py          ~110  Audio-Download plus faster-whisper
│   ├── storage.py          ~120  atomares Schreiben, JSON-Sidecar
│   ├── naming.py           ~90   Slug-Regeln, Kollisionsauflösung
│   ├── pipeline.py         ~140  Orchestrierung Sync und Verarbeitung
│   ├── locking.py          ~60   Lockfile
│   └── logsetup.py         ~50   Handler-Konfiguration
└── tests/
```

Regel für den Schnitt: `pipeline.py` kennt die Reihenfolge, weiß aber nichts
über yt-dlp oder VTT. `sources/*` liefern ein `TranscriptResult` oder `None` und
kennen weder DB noch Dateisystem. Das hält die Whisper-Abhängigkeit isoliert und
macht die Pipeline ohne Netz testbar.

---

## 13. Abhängigkeiten

```bash
uv init
uv add typer rich httpx feedparser yt-dlp platformdirs pydantic pydantic-settings
uv add --optional asr faster-whisper
uv add --dev pytest pytest-cov ruff mypy
```

| Paket | Zweck |
|-------|-------|
| `typer` + `rich` | CLI und Konsolenausgabe |
| `httpx` | Feed-Abruf mit Conditional GET |
| `feedparser` | Atom-Parsing inklusive der `yt:`-Namespaces |
| `yt-dlp` | Untertitel und Metadaten, als Bibliothek eingebunden |
| `platformdirs` | plattformkonforme Default-Verzeichnisse |
| `pydantic-settings` | Config aus TOML und ENV, mit Validierung |
| `faster-whisper` | optional, nur ASR-Pfad |

Betrieb auf dem Server:

```bash
uv sync --extra asr      # mit ASR
uv sync                  # nur Caption-Pfad
uv run ytdigest run --limit 20
```

yt-dlp braucht regelmäßige Updates, weil YouTube die Player-Signaturen häufig
ändert. Auf dem Server gehört ein wöchentliches `uv lock --upgrade-package yt-dlp`
plus `uv sync` in die Wartungsroutine, sonst bricht der Abruf irgendwann still.

---

## 14. Teststrategie

- Keine Netzwerkzugriffe in Tests. Feed-Antworten und VTT-Dateien liegen als
  Fixtures unter `tests/fixtures/`.
- `sources/*` werden über Protokolle abstrahiert und in Pipeline-Tests durch
  Fakes ersetzt.
- Schwerpunkte:
  - `naming.py`: Umlaute, reservierte Namen, Überlänge, leerer Titel, Kollision
  - `sources/vtt.py`: doppelte Zeilen, Inline-Tags, leere Datei
  - `feeds/parser.py`: Kommentare, Handles, Duplikate, Anzeigename-Override
  - `pipeline.py`: Erstlauf-Modi, Abbruch mitten im Lauf, Retry-Logik
  - `db/repo.py`: Zustandsübergänge, `processing`-Reset beim Start
- CI läuft gegen Windows und Linux, weil die Pfad- und Encoding-Logik genau dort
  auseinanderläuft.

---

## 15. Offene Punkte

| Nr. | Frage | Empfehlung |
|-----|-------|------------|
| 1 | Cookies für altersbeschränkte Videos? | Erst umsetzen, wenn es real auftritt. `--cookies-from-browser` funktioniert headless auf dem Server ohnehin schlecht |
| 2 | Aufräumen alter Transkripte? | Vorerst nein, Textdateien sind klein |
| 3 | Sprache pro Feed konfigurierbar? | Sinnvoll, sobald deutsche und englische Kanäle gemischt werden. Als optionale dritte Spalte in `feeds.txt` nachrüstbar |
| 4 | Dauerfilter pro Feed? | Erst bei Bedarf. 8 Minuten global ist ein guter Start, ein Kanal mit systematisch kürzeren, aber gehaltvollen Videos wäre der Auslöser |

---

## 16. Ausblick: LLM-Zusammenfassung

Der Folgeschritt braucht keine Änderung am Datenmodell:

- Kandidaten kommen aus `SELECT … WHERE status='done' AND summary_status='none'`
- Der `.json`-Sidecar enthält alles, was für einen Prompt-Kontext nötig ist
- Ergebnisse landen als `*.summary.md` neben dem Transkript, `summary_status`
  wandert auf `done`

Das rechtfertigt ein eigenes Kommando `ytdigest summarize` im selben Paket statt
eines zweiten Tools, weil DB, Config und Ablage identisch bleiben.
