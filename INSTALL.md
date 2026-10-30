# Installationsanleitung

Zwei Szenarien:

1. [Alles auf einem Gerät](#1-alles-auf-einem-gerät) - einfachster Fall.
2. [Verteilt: Raspberry Pi + GPU-Hauptrechner](#2-verteilt-raspberry-pi--gpu-hauptrechner) -
   Web-Oberfläche/Datenbank dauerhaft auf einem Pi, ASR/Ollama nur bei Bedarf
   auf dem Hauptrechner im selben LAN.

Hintergrund zum verteilten Modell steht auch im
[README unter "Verteilter Betrieb"](README.md#verteilter-betrieb); hier die
Schritt-für-Schritt-Variante dazu.

## 1. Alles auf einem Gerät

```bash
uv sync --extra asr --extra web   # ASR-Fallback + Web-Oberfläche
uv run ytdigest init
uv run ytdigest feeds add @t3dotgg
uv run ytdigest run --limit 20
uv run ytdigest web serve
```

Der ASR-Pfad braucht zusätzlich `ffmpeg`/`ffprobe` im `PATH`. Für
regelmäßiges Syncen `ytdigest run` per Cron (Linux/macOS) oder Taskplaner
(Windows) aufrufen - siehe [README](README.md#nutzung).

## 2. Verteilt: Raspberry Pi + GPU-Hauptrechner

### 2.1 GPU-Hauptrechner (Windows 11): ASR + Ollama

**`ffmpeg`** (Pflicht für den ASR-Pfad, liefert auch `ffprobe`):

```powershell
winget install ffmpeg
```

Danach neues Terminal öffnen, damit die PATH-Änderung greift; prüfen mit
`ffmpeg -version`.

**Ollama** läuft unter Windows als Hintergrunddienst mit Tray-Icon und bindet
standardmäßig nur auf `127.0.0.1` - für Zugriff vom Pi aus muss `OLLAMA_HOST`
dauerhaft als Windows-Umgebungsvariable gesetzt werden:

```powershell
setx OLLAMA_HOST "0.0.0.0"
ollama pull qwen3.5:9b
```

Danach Ollama über das Tray-Icon beenden ("Quit Ollama") und neu starten
(oder abmelden/anmelden), damit es die Variable übernimmt - ein bloßes
`ollama serve` reicht nicht, wenn im Hintergrund schon eine Instanz läuft.

**Windows Firewall:** beim ersten eingehenden Zugriff kommt normalerweise ein
Freigabe-Dialog; alternativ vorab per PowerShell (als Administrator):

```powershell
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow
New-NetFirewallRule -DisplayName "ytdigest ASR-Worker" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
```

**ASR-Worker:**

```powershell
cd sourcerer
uv sync --extra asr --extra web
```

In der `config.toml` auf dem Hauptrechner:

```toml
[asr_worker]
host = "0.0.0.0"
port = 8765
allowed_roots = ["~/Dokumente/sourcerer/*"]   # nur nötig für lokale Aufnahmeordner,
                                              # die der Pi per "local:http://..."-Feed
                                              # mitsynct; Glob-Muster, kein Neustart
                                              # nötig wenn neue Unterordner entstehen -
                                              # unter Windows z. B.
                                              # "C:/Users/<name>/Dokumente/sourcerer/*"
```

Starten:

```powershell
uv run ytdigest asr-worker serve --host 0.0.0.0 --port 8765
```

Erreichbarkeit von einem anderen Gerät im LAN prüfen (z. B. vom Pi aus):

```bash
curl http://<ip-des-hauptrechners>:8765/health
```

**Dauerhaft im Hintergrund laufen lassen** (optional - ohne das läuft der
Worker nur solange das Terminal offen ist): Taskplaner-Aufgabe anlegen,
Trigger "Bei der Anmeldung", Aktion `uv run ytdigest asr-worker serve --host
0.0.0.0 --port 8765` mit "Starten in" = Projektordner (`cd sourcerer`-Pfad).
Ollama selbst startet über sein eigenes Tray-Icon/Autostart bereits bei
Login.

Ist der Hauptrechner aus, merkt der Pi das beim nächsten Sync (ASR-/
Ollama-Schritt schlägt fehl, Video landet als `failed` mit automatischem
Retry) - kein Problem, kein automatisches Nachpollen in kurzen Abständen.
Einfach Worker/Ollama starten, wenn gebraucht, dann `ytdigest retry` bzw.
den Retry-Button in der Web-Oberfläche nutzen.

### 2.2 Raspberry Pi (Web-Oberfläche + Datenbank + Transkripte)

```bash
git clone <dein-repo> sourcerer
cd sourcerer
uv sync --extra web        # kein --extra asr nötig, läuft remote über den Worker
uv run ytdigest init
```

In der `config.toml` auf dem Pi:

```toml
[asr]
remote_url = "http://<ip-des-hauptrechners>:8765"

[summary]
ollama_host = "http://<ip-des-hauptrechners>:11434"

[web]
host = "0.0.0.0"           # damit die Seite im LAN erreichbar ist, nicht nur lokal auf dem Pi
port = 5050
```

Feeds eintragen, Sync testen:

```bash
uv run ytdigest feeds add @t3dotgg
uv run ytdigest run --limit 5
```

Optional: Ordner, die über `asr_worker.allowed_roots` auf dem Hauptrechner
freigegeben sind, direkt als Feed eintragen (kein Samba/NFS nötig):

```
local:http://<ip-des-hauptrechners>:8765/folders/fom_s4 | FOM S4
```

#### systemd-Unit (Web-Oberfläche)

Damit die Seite nach einem Reboot automatisch wieder läuft:

```ini
# /etc/systemd/system/ytdigest-web.service
[Unit]
Description=ytdigest Web-UI
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/sourcerer
ExecStart=/home/pi/.local/bin/uv run ytdigest web serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now ytdigest-web
```

#### Regelmäßiges Syncen per Cron

```cron
*/30 * * * * cd /home/pi/sourcerer && uv run ytdigest run --limit 20 >> /home/pi/sourcerer/run.log 2>&1
```

Danach ist die Seite unter `http://<ip-des-pi>:5050` aus dem gesamten LAN
erreichbar; Sync/ASR/Ollama laufen transparent über den Hauptrechner, sobald
der läuft.

### 2.3 Sicherheitshinweis

Weder der ASR-Worker noch Ollama noch die Web-Oberfläche haben eine
Zugangskontrolle (kein Login vorgesehen - nur explizit angeforderter
Netzwerkzugriff). Alle drei nur im eigenen LAN erreichbar machen, nie per
Portweiterleitung ins Internet legen.
