from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ytdigest.sources import local


def _touch(path: Path, content: bytes = b"x") -> Path:
    path.write_bytes(content)
    return path


def test_iter_channel_dirs_only_direct_subdirs(tmp_path):
    (tmp_path / "kanal_a").mkdir()
    (tmp_path / "kanal_b").mkdir()
    _touch(tmp_path / "not_a_channel.mp4")  # Datei auf Root-Ebene: kein Kanal
    (tmp_path / "kanal_a" / "nested").mkdir()  # zweite Ebene wird nicht gelistet

    names = [d.name for d in local.iter_channel_dirs(tmp_path)]
    assert names == ["kanal_a", "kanal_b"]


def test_iter_channel_dirs_missing_root(tmp_path):
    assert list(local.iter_channel_dirs(tmp_path / "missing")) == []


def test_iter_video_files_filters_by_extension(tmp_path):
    _touch(tmp_path / "a.mp4")
    _touch(tmp_path / "b.MKV")  # Groß-/Kleinschreibung darf keine Rolle spielen
    _touch(tmp_path / "notes.txt")

    names = [p.name for p in local.iter_video_files(tmp_path, ["mp4", "mkv"])]
    assert names == ["a.mp4", "b.MKV"]


def test_is_settled(tmp_path):
    path = _touch(tmp_path / "video.mp4")
    assert local.is_settled(path, min_age_s=0)
    assert not local.is_settled(path, min_age_s=999999)


def test_is_settled_missing_file(tmp_path):
    assert not local.is_settled(tmp_path / "gone.mp4", min_age_s=0)


def test_feed_url_and_is_local_feed(tmp_path):
    channel_dir = tmp_path / "kanal_a"
    channel_dir.mkdir()
    url = local.feed_url_for(channel_dir)
    assert url.startswith("local:")
    assert str(channel_dir.resolve()) in url
    assert local.is_local_feed(url)
    assert not local.is_local_feed("https://example.com/feed.xml")


def _fake_run(stdout_obj):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(stdout_obj), stderr="")
    return _run


def test_probe_reads_duration(tmp_path, monkeypatch):
    path = _touch(tmp_path / "video.mp4")
    monkeypatch.setattr(local.subprocess, "run", _fake_run({"format": {"duration": "125.4"}}))
    result = local.probe(path)
    assert result.duration_s == 125
    assert result.error is None


def test_probe_missing_file(tmp_path):
    result = local.probe(tmp_path / "gone.mp4")
    assert result.error == "missing"


def test_probe_ffprobe_failure(tmp_path, monkeypatch):
    path = _touch(tmp_path / "video.mp4")

    def _boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(local.subprocess, "run", _boom)
    result = local.probe(path)
    assert result.error == "ffprobe_failed"


def test_creation_time_prefers_embedded_metadata(tmp_path, monkeypatch):
    path = _touch(tmp_path / "video.mp4")
    monkeypatch.setattr(
        local.subprocess, "run",
        _fake_run({"format": {"tags": {"creation_time": "2026-01-02T03:04:05.000000Z"}}}),
    )
    dt = local.creation_time(path)
    assert dt == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_creation_time_falls_back_to_stat(tmp_path, monkeypatch):
    path = _touch(tmp_path / "video.mp4")
    monkeypatch.setattr(local.subprocess, "run", _fake_run({"format": {}}))
    dt = local.creation_time(path)
    assert isinstance(dt, datetime)
    assert dt.tzinfo is UTC


@pytest.mark.parametrize("bad_extension", ["mp4", "MP4", ".mp4"])
def test_iter_video_files_accepts_dot_or_bare_extension(tmp_path, bad_extension):
    _touch(tmp_path / "a.mp4")
    names = [p.name for p in local.iter_video_files(tmp_path, [bad_extension])]
    assert names == ["a.mp4"]


def test_parse_local_path_posix():
    assert local.parse_local_path("/home/me/aufnahmen") == Path("/home/me/aufnahmen")


def test_parse_local_path_windows_backslash_normalised():
    assert local.parse_local_path(r"C:\Users\me\Aufnahmen") == Path("C:/Users/me/Aufnahmen")


def test_parse_local_path_strips_whitespace():
    assert local.parse_local_path("  /tmp/x  ") == Path("/tmp/x")


def test_existing_transcript_path_missing(tmp_path):
    media = _touch(tmp_path / "clip.mp3")
    assert local.existing_transcript_path(media) is None


def test_existing_transcript_path_found(tmp_path):
    media = _touch(tmp_path / "clip.mp3")
    txt = tmp_path / "clip.txt"
    txt.write_text("hallo welt", encoding="utf-8")
    assert local.existing_transcript_path(media) == txt


def test_read_existing_transcript_adopts_text(tmp_path):
    media = _touch(tmp_path / "clip.wav")
    (tmp_path / "clip.txt").write_text("  bereits transkribierter text  \n",
                                       encoding="utf-8")
    result = local.read_existing_transcript(media)
    assert result is not None
    assert result.text == "bereits transkribierter text"
    assert result.source == "local_txt"


def test_read_existing_transcript_none_without_sidecar(tmp_path):
    media = _touch(tmp_path / "clip.wav")
    assert local.read_existing_transcript(media) is None


def test_read_existing_transcript_none_for_empty_sidecar(tmp_path):
    media = _touch(tmp_path / "clip.wav")
    (tmp_path / "clip.txt").write_text("   ", encoding="utf-8")
    assert local.read_existing_transcript(media) is None
