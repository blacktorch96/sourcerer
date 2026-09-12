"""Config-Laden: --config-Pfad, dann ./config.toml, dann Benutzer-Config-Verzeichnis.

Jede Option ist zusätzlich per Umgebungsvariable ``YTDIGEST_<SEKTION>__<KEY>``
überschreibbar, z. B. ``YTDIGEST_ASR__DEVICE=cpu``.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import platformdirs
from pydantic import BaseModel, Field

APP_NAME = "ytdigest"
ENV_PREFIX = "YTDIGEST_"


class Paths(BaseModel):
    feeds_file: Path = Path("feeds.txt")
    database: Path = Path("data/ytdigest.sqlite3")
    output_dir: Path = Path("transkripte")
    temp_dir: str = ""
    log_file: Path = Path("logs/ytdigest.log")


class FeedsCfg(BaseModel):
    request_timeout_s: int = 15
    delay_between_s: float = 2.0
    use_conditional_get: bool = True


class TranscriptsCfg(BaseModel):
    prefer_original: bool = True   # immer die tatsächliche Videosprache, nie Auto-Übersetzung
    languages: list[str] = ["de", "en"]  # nur Fallback, falls Originalsprache unbestimmbar
    prefer_manual: bool = True
    max_attempts: int = 3
    retry_backoff_s: list[int] = [60, 300, 1800]
    paragraph_pause_s: float = 1.8   # Pause zwischen Cues ab der ein neuer Absatz beginnt, 0 = aus
    chapter_headings: bool = True    # YouTube-Kapitel (falls vorhanden) als Überschriften einfügen


class FiltersCfg(BaseModel):
    min_duration_min: int = 8


class AsrCfg(BaseModel):
    enabled: bool = True
    model: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    beam_size: int = 5
    max_duration_min: int = 90
    paragraph_pause_s: float = 1.8   # Sprechpause ab der ein neuer Absatz beginnt, 0 = aus


class OutputCfg(BaseModel):
    include_video_id: bool = False
    max_filename_len: int = 120
    timestamp_source: str = "published_utc"
    line_width: int = 120   # Zeilenumbruch im Transkript-Text, 0 = kein Umbruch


class LocalCfg(BaseModel):
    extensions: list[str] = ["mp4", "mkv", "webm", "mov", "avi", "m4v"]
    min_age_s: int = 30   # Datei muss seit mind. so vielen Sekunden unverändert sein
                          # (wartet einen laufenden Kopiervorgang ab)


class GDriveCfg(BaseModel):
    enabled: bool = False
    service_account_file: Path = Path("service_account.json")
    folder_id: str = ""          # ID des freigegebenen Zielordners in Google Drive
    mirror_subdirs: bool = True  # je Kanal einen Unterordner (wie dir_slug lokal) anlegen
    upload_sidecar: bool = True  # zusätzlich zur .txt auch die .json-Metadatei hochladen


class Config(BaseModel):
    paths: Paths = Field(default_factory=Paths)
    feeds: FeedsCfg = Field(default_factory=FeedsCfg)
    transcripts: TranscriptsCfg = Field(default_factory=TranscriptsCfg)
    filters: FiltersCfg = Field(default_factory=FiltersCfg)
    asr: AsrCfg = Field(default_factory=AsrCfg)
    output: OutputCfg = Field(default_factory=OutputCfg)
    local: LocalCfg = Field(default_factory=LocalCfg)
    gdrive: GDriveCfg = Field(default_factory=GDriveCfg)

    source_path: Path | None = None


def _candidate_paths(explicit: Path | None) -> list[Path]:
    out: list[Path] = []
    if explicit:
        out.append(explicit)
    out.append(Path("config.toml"))
    out.append(platformdirs.user_config_path(APP_NAME) / "config.toml")
    return out


def _find_config(explicit: Path | None) -> Path | None:
    for path in _candidate_paths(explicit):
        if path.is_file():
            return path
    return None


def _apply_env(data: dict) -> dict:
    """``YTDIGEST_ASR__DEVICE=cpu`` -> data['asr']['device'] = 'cpu'."""
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        remainder = key[len(ENV_PREFIX):].lower()
        parts = remainder.split("__")
        if len(parts) != 2:
            continue
        section, option = parts
        data.setdefault(section, {})[option] = value
    return data


def load_config(explicit: Path | None = None) -> Config:
    path = _find_config(explicit)
    data: dict = {}
    if path is not None:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    data = _apply_env(data)
    cfg = Config.model_validate(data)
    cfg.source_path = path
    return cfg
