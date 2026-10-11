from __future__ import annotations

from pathlib import Path

from ytdigest import config_writer


def test_update_values_creates_file_when_missing(tmp_path: Path):
    path = tmp_path / "config.toml"
    config_writer.update_values(path, {"filters": {"min_duration_min": 5}})

    assert path.is_file()
    assert "min_duration_min = 5" in path.read_text(encoding="utf-8")


def test_update_values_preserves_comments_and_other_sections(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        "# Kommentar, der erhalten bleiben soll\n"
        "[paths]\n"
        "database = \"data/ytdigest.sqlite3\"\n\n"
        "[filters]\n"
        "min_duration_min = 8  # Shorts rausfiltern\n",
        encoding="utf-8",
    )

    config_writer.update_values(path, {"filters": {"min_duration_min": 15}})
    content = path.read_text(encoding="utf-8")

    assert "# Kommentar, der erhalten bleiben soll" in content
    assert 'database = "data/ytdigest.sqlite3"' in content
    assert "min_duration_min = 15" in content
    assert "# Shorts rausfiltern" in content
    assert "min_duration_min = 8" not in content


def test_update_values_adds_missing_section(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("[paths]\ndatabase = \"x.sqlite3\"\n", encoding="utf-8")

    config_writer.update_values(path, {"summary": {"model": "qwen3.5:9b"}})
    content = path.read_text(encoding="utf-8")

    assert "[summary]" in content
    assert 'model = "qwen3.5:9b"' in content
    assert 'database = "x.sqlite3"' in content
