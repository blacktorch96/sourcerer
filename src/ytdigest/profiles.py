"""Profile: mehrere unabhängige Recherchebereiche (z. B. 'technologie',
'studium'), jedes eine eigene, vollständige config.toml unter
profiles/<name>/config.toml - eigene Feeds, Datenbank, Transkript-Ordner,
ASR-/Zusammenfassungs-Einstellungen. Ohne '--profile'/Profilwechsel verhält
sich das Tool unverändert wie zuvor (normale --config/./config.toml-Auflösung,
siehe config.py).

Kein eigenes Datenmodell nötig: ein Profil *ist* einfach eine weitere
config.toml, die Pipeline/Repo/Web-Views kennen den Unterschied nicht - sie
bekommen immer nur "die aktuelle Config" gereicht.
"""

from __future__ import annotations

from pathlib import Path

from ytdigest.config import Config

PROFILES_DIR = Path("profiles")


def profile_config_path(name: str, base: Path | None = None) -> Path:
    return (base or PROFILES_DIR) / name / "config.toml"


def list_profiles(base: Path | None = None) -> list[str]:
    """Namen aller gefundenen Profile (Ordnername unter profiles/, der eine
    config.toml enthält), alphabetisch. Leere Liste, wenn es den Ordner
    (noch) nicht gibt - kein Fehler, Profile sind rein optional."""
    root = base or PROFILES_DIR
    if not root.is_dir():
        return []
    return sorted(p.parent.name for p in root.glob("*/config.toml"))


def current_profile_name(cfg: Config, base: Path | None = None) -> str | None:
    """Name des Profils, aus dem ``cfg`` geladen wurde, oder None, wenn cfg
    nicht aus profiles/<name>/config.toml stammt (z. B. normale ./config.toml
    ohne --profile)."""
    if cfg.source_path is None:
        return None
    root = (base or PROFILES_DIR).resolve()
    try:
        resolved = cfg.source_path.resolve()
    except OSError:
        return None
    if resolved.parent.parent != root:
        return None
    return resolved.parent.name
