"""Tests für asr_worker.py - der isolierte HTTP-Dienst für den verteilten
Betrieb (siehe Moduldoc dort). EXPERIMENTELL/eigenständig."""

from __future__ import annotations

import pytest

from ytdigest import asr_worker
from ytdigest.models import TranscriptResult, VideoProbe
from ytdigest.sources import asr


@pytest.fixture
def worker_cfg(cfg, tmp_path):
    cfg.asr_worker.allowed_roots = [str(tmp_path / "freigegeben" / "*")]
    return cfg


@pytest.fixture
def client(worker_cfg):
    app = asr_worker.create_worker_app(worker_cfg)
    app.config.update(TESTING=True)
    return app.test_client()


def _make_folder(tmp_path, name: str) -> None:
    (tmp_path / "freigegeben" / name).mkdir(parents=True)


def test_resolve_file_rejects_traversal_outside_folder(worker_cfg, tmp_path):
    _make_folder(tmp_path, "kurs_a")
    (tmp_path / "secret.txt").write_text("geheim")

    assert asr_worker._resolve_file(worker_cfg.asr_worker, "kurs_a",
                                    "../secret.txt") is None


def test_resolve_file_accepts_direct_child(worker_cfg, tmp_path):
    _make_folder(tmp_path, "kurs_a")
    (tmp_path / "freigegeben" / "kurs_a" / "a.mp3").write_bytes(b"x")

    resolved = asr_worker._resolve_file(worker_cfg.asr_worker, "kurs_a", "a.mp3")
    assert resolved == (tmp_path / "freigegeben" / "kurs_a" / "a.mp3").resolve()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_list_folders_empty_when_nothing_matches(client):
    resp = client.get("/folders")
    assert resp.get_json() == {"folders": []}


def test_list_folders_lists_matching_subdirs(client, tmp_path):
    _make_folder(tmp_path, "kurs_a")
    _make_folder(tmp_path, "kurs_b")
    resp = client.get("/folders")
    ids = sorted(f["id"] for f in resp.get_json()["folders"])
    assert ids == ["kurs_a", "kurs_b"]


def test_list_files_unknown_folder_404(client):
    resp = client.get("/folders/unbekannt/files")
    assert resp.status_code == 404


def test_list_files_filters_settled_and_extension(client, tmp_path, worker_cfg):
    _make_folder(tmp_path, "kurs_a")
    folder = tmp_path / "freigegeben" / "kurs_a"
    (folder / "lecture1.mp3").write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"x")
    worker_cfg.local.min_age_s = 0

    resp = client.get("/folders/kurs_a/files")
    files = resp.get_json()["files"]
    assert [f["filename"] for f in files] == ["lecture1.mp3"]
    assert "created_at" in files[0]


def test_probe_missing_file_returns_missing_error(client, tmp_path):
    # Pfad lässt sich auflösen (liegt unter dem freigegebenen Ordner), die
    # Datei selbst existiert aber nicht - local.probe() liefert dafür schon
    # heute 'error=missing' statt eines Exceptions, das bleibt auch remote so.
    _make_folder(tmp_path, "kurs_a")
    resp = client.get("/folders/kurs_a/files/gone.mp3/probe")
    assert resp.status_code == 200
    assert resp.get_json() == {"duration_s": None, "error": "missing"}


def test_probe_path_traversal_rejected(client, tmp_path):
    _make_folder(tmp_path, "kurs_a")
    (tmp_path / "secret.txt").write_text("geheim")
    resp = client.get("/folders/kurs_a/files/../../secret.txt/probe")
    assert resp.status_code == 404


def test_probe_known_file(client, tmp_path, monkeypatch):
    _make_folder(tmp_path, "kurs_a")
    folder = tmp_path / "freigegeben" / "kurs_a"
    (folder / "lecture1.mp3").write_bytes(b"x")
    monkeypatch.setattr(asr_worker.local, "probe", lambda path: VideoProbe(duration_s=42))

    resp = client.get("/folders/kurs_a/files/lecture1.mp3/probe")
    assert resp.get_json() == {"duration_s": 42, "error": None}


def test_transcript_not_found(client, tmp_path):
    _make_folder(tmp_path, "kurs_a")
    folder = tmp_path / "freigegeben" / "kurs_a"
    (folder / "lecture1.mp3").write_bytes(b"x")
    resp = client.get("/folders/kurs_a/files/lecture1.mp3/transcript")
    assert resp.status_code == 404


def test_transcript_found(client, tmp_path):
    _make_folder(tmp_path, "kurs_a")
    folder = tmp_path / "freigegeben" / "kurs_a"
    (folder / "lecture1.mp3").write_bytes(b"x")
    (folder / "lecture1.txt").write_text("bereits transkribiert", encoding="utf-8")

    resp = client.get("/folders/kurs_a/files/lecture1.mp3/transcript")
    data = resp.get_json()
    assert data["text"] == "bereits transkribiert"
    assert data["source"] == "local_txt"


def test_transcribe_file_success(client, tmp_path, worker_cfg, monkeypatch):
    _make_folder(tmp_path, "kurs_a")
    folder = tmp_path / "freigegeben" / "kurs_a"
    (folder / "lecture1.mp3").write_bytes(b"x")
    worker_cfg.asr.enabled = True
    monkeypatch.setattr(
        asr_worker.asr, "transcribe_local",
        lambda path, *, cfg: TranscriptResult(text="Text", source="asr",
                                              language="de", asr_model="large-v3"),
    )

    resp = client.post("/folders/kurs_a/files/lecture1.mp3/transcribe")
    data = resp.get_json()
    assert data["text"] == "Text"
    assert data["asr_model"] == "large-v3"


def test_transcribe_file_disabled(client, tmp_path, worker_cfg):
    _make_folder(tmp_path, "kurs_a")
    (tmp_path / "freigegeben" / "kurs_a" / "lecture1.mp3").write_bytes(b"x")
    worker_cfg.asr.enabled = False

    resp = client.post("/folders/kurs_a/files/lecture1.mp3/transcribe")
    assert resp.status_code == 503


def test_transcribe_file_unavailable(client, tmp_path, worker_cfg, monkeypatch):
    _make_folder(tmp_path, "kurs_a")
    (tmp_path / "freigegeben" / "kurs_a" / "lecture1.mp3").write_bytes(b"x")
    worker_cfg.asr.enabled = True

    def _boom(path, *, cfg):
        raise asr.AsrUnavailable("faster-whisper fehlt")

    monkeypatch.setattr(asr_worker.asr, "transcribe_local", _boom)
    resp = client.post("/folders/kurs_a/files/lecture1.mp3/transcribe")
    assert resp.status_code == 503


def test_transcribe_remote_missing_url(client, worker_cfg):
    worker_cfg.asr.enabled = True
    resp = client.post("/transcribe", json={})
    assert resp.status_code == 400


def test_transcribe_remote_youtube_dispatch(client, worker_cfg, monkeypatch):
    worker_cfg.asr.enabled = True
    captured = {}

    def fake_transcribe(url, *, cfg, temp_dir):
        captured["url"] = url
        return TranscriptResult(text="yt", source="asr", language="de")

    monkeypatch.setattr(asr_worker.asr, "transcribe", fake_transcribe)
    resp = client.post("/transcribe", json={"url": "https://youtube.com/watch?v=x"})
    assert resp.get_json()["text"] == "yt"
    assert captured["url"] == "https://youtube.com/watch?v=x"


def test_transcribe_remote_podcast_dispatch(client, worker_cfg, monkeypatch):
    worker_cfg.asr.enabled = True
    captured = {}

    def fake_transcribe(url, *, cfg, temp_dir):
        captured["url"] = url
        return TranscriptResult(text="podcast", source="asr", language="de")

    monkeypatch.setattr(asr_worker.podcast, "transcribe", fake_transcribe)
    resp = client.post("/transcribe", json={"url": "https://example.com/e.mp3", "kind": "podcast"})
    assert resp.get_json()["text"] == "podcast"
    assert captured["url"] == "https://example.com/e.mp3"


def test_transcribe_remote_no_transcript(client, worker_cfg, monkeypatch):
    worker_cfg.asr.enabled = True
    monkeypatch.setattr(asr_worker.asr, "transcribe", lambda url, *, cfg, temp_dir: None)
    resp = client.post("/transcribe", json={"url": "https://youtube.com/watch?v=x"})
    assert resp.status_code == 422
