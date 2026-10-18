from __future__ import annotations

from pathlib import Path

from ytdigest.config import Config
from ytdigest.profiles import current_profile_name, list_profiles, profile_config_path


def test_list_profiles_empty_when_dir_missing(tmp_path: Path):
    assert list_profiles(base=tmp_path / "no-such-dir") == []


def test_list_profiles_finds_subfolders_with_config(tmp_path: Path):
    (tmp_path / "technologie").mkdir()
    (tmp_path / "technologie" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "studium").mkdir()
    (tmp_path / "studium" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "leer").mkdir()  # kein config.toml drin -> kein Profil

    assert list_profiles(base=tmp_path) == ["studium", "technologie"]


def test_profile_config_path(tmp_path: Path):
    assert profile_config_path("studium", base=tmp_path) == tmp_path / "studium" / "config.toml"


def test_current_profile_name_none_without_source_path():
    cfg = Config()
    assert cfg.source_path is None
    assert current_profile_name(cfg) is None


def test_current_profile_name_none_outside_profiles_dir(tmp_path: Path):
    cfg = Config()
    cfg.source_path = tmp_path / "config.toml"
    assert current_profile_name(cfg, base=tmp_path / "profiles") is None


def test_current_profile_name_detects_profile(tmp_path: Path):
    profiles_dir = tmp_path / "profiles"
    (profiles_dir / "studium").mkdir(parents=True)
    cfg = Config()
    cfg.source_path = profiles_dir / "studium" / "config.toml"

    assert current_profile_name(cfg, base=profiles_dir) == "studium"
