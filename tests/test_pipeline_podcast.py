from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ytdigest import pipeline as pl
from ytdigest.db.repo import Repo
from ytdigest.feeds.fetcher import FeedFetchResult
from ytdigest.models import FeedEntry, TranscriptResult
from ytdigest.pipeline import Pipeline, RunOptions

_FEED_URL = "https://rss.buzzsprout.com/2402174.rss"
_CHANNEL_ID = f"podcast:{_FEED_URL}"


def _entries():
    return [
        FeedEntry("ep-neu", "Neue Folge", datetime(2026, 8, 30, tzinfo=UTC),
                  "https://cdn.example.com/audio/neu.mp3",
                  channel_id=_CHANNEL_ID, duration_s=1500),
        FeedEntry("ep-alt", "Alte kurze Folge", datetime(2026, 8, 20, tzinfo=UTC),
                  "https://cdn.example.com/audio/alt.mp3",
                  channel_id=_CHANNEL_ID, duration_s=120),
    ]


@pytest.fixture
def podcast_patched(cfg, monkeypatch):
    cfg.asr.enabled = True  # conftest deaktiviert ASR global; hier gebraucht
    monkeypatch.setattr(pl, "fetch_podcast_feed", lambda *a, **k: FeedFetchResult(
        entries=_entries(), channel_id=_CHANNEL_ID, channel_title="Mein Podcast"))
    monkeypatch.setattr(pl.podcast, "transcribe", lambda url, *, cfg, temp_dir: TranscriptResult(
        text="ein sauberer fließtext hier", source="asr", language="de"))
    return monkeypatch


def _write_feeds(cfg, line: str = f"podcast:{_FEED_URL} | Mein Podcast\n") -> None:
    cfg.paths.feeds_file.write_text(line, encoding="utf-8")


def test_podcast_line_is_ingested_and_transcribed_via_asr(cfg, conn, podcast_patched):
    _write_feeds(cfg)
    report = Pipeline(cfg, conn).run(RunOptions(initial_mode="backfill"))

    repo = Repo(conn)
    feed = repo.get_feed_by_channel_id(_CHANNEL_ID)
    assert feed is not None
    assert feed.channel_title == "Mein Podcast"

    neu = repo.get_video("ep-neu")
    assert neu.status == "done"
    assert neu.source == "asr"
    assert neu.duration_s == 1500

    alt = repo.get_video("ep-alt")
    assert alt.status == "skipped"
    assert alt.skip_reason == "too_short"

    assert report.processed == 1
    assert report.via_asr == 1


def test_podcast_duration_from_feed_skips_min_duration_probe(
    cfg, conn, podcast_patched, monkeypatch,
):
    """Dauer kommt aus itunes:duration - captions.probe (yt-dlp) darf für
    Podcast-Feeds beim initialen 'latest'-Modus nicht aufgerufen werden."""
    def boom(url):
        raise AssertionError("captions.probe darf für Podcasts nicht aufgerufen werden")

    monkeypatch.setattr(pl.captions, "probe", boom)
    _write_feeds(cfg)
    report = Pipeline(cfg, conn).run(RunOptions(initial_mode="latest"))

    assert Repo(conn).get_video("ep-neu").status == "done"
    assert report.processed == 1


def test_podcast_second_run_only_processes_new(cfg, conn, podcast_patched):
    _write_feeds(cfg)
    Pipeline(cfg, conn).run(RunOptions(initial_mode="mark-seen"))
    report = Pipeline(cfg, conn).run(RunOptions())
    assert report.new_videos == 0
