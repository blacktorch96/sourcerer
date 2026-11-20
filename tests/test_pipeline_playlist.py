from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ytdigest import pipeline as pl
from ytdigest.db.repo import Repo
from ytdigest.feeds.fetcher import FeedFetchResult
from ytdigest.models import FeedEntry, VideoProbe
from ytdigest.pipeline import Pipeline, RunOptions
from ytdigest.sources import playlist

_PLAYLIST_ID = "PLtest0123456789"
_CHANNEL_ID = playlist.channel_id_for(_PLAYLIST_ID)


def _rss_entry(video_id: str, day: int) -> FeedEntry:
    return FeedEntry(video_id, f"Titel {video_id}", datetime(2026, 9, day, tzinfo=UTC),
                     f"https://www.youtube.com/watch?v={video_id}")


@pytest.fixture
def pl_patched(cfg, monkeypatch):
    """RSS: zwei Einträge; Gesamtliste: diese zwei plus zwei Ältere, ein
    privates Video und ein weiteres. Datums-Probe je Video aus ``dates``."""
    cfg.feeds.delay_between_s = 0
    state = {
        "rss": [_rss_entry("rss00000001", 20), _rss_entry("rss00000002", 19)],
        "listed": [
            {"video_id": "rss00000001", "title": "Titel rss00000001"},
            {"video_id": "rss00000002", "title": "Titel rss00000002"},
            {"video_id": "old00000001", "title": "Alt 1"},
            {"video_id": "priv0000001", "title": "[Private video]"},
            {"video_id": "old00000002", "title": "Alt 2"},
        ],
        "dates": {"old00000001": "20240301", "old00000002": "20230101",
                  "rss00000001": "20260920", "rss00000002": "20260919"},
        "list_calls": 0,
        "probed": [],
        "list_error": None,
    }

    monkeypatch.setattr(pl, "fetch_feed", lambda *a, **k: FeedFetchResult(
        entries=list(state["rss"]), channel_title="Meine Playlist"))

    def fake_list(playlist_id):
        state["list_calls"] += 1
        assert playlist_id == _PLAYLIST_ID
        if state["list_error"]:
            raise RuntimeError(state["list_error"])
        return list(state["listed"])

    def fake_probe(url):
        video_id = url.rsplit("=", 1)[-1]
        state["probed"].append(video_id)
        return VideoProbe(duration_s=900,
                          info={"upload_date": state["dates"].get(video_id)})

    monkeypatch.setattr(pl.channel, "list_playlist_videos", fake_list)
    monkeypatch.setattr(pl.captions, "probe", fake_probe)
    return state


def _write_feeds(cfg) -> None:
    cfg.paths.feeds_file.write_text(f"playlist:{_PLAYLIST_ID} | Playlist\n", encoding="utf-8")


def _sync(cfg, conn, **kwargs):
    return Pipeline(cfg, conn).run(RunOptions(sync_only=True, **kwargs))


def test_first_sync_checks_complete_list_when_all_rss_entries_unknown(cfg, conn, pl_patched):
    _write_feeds(cfg)
    report = _sync(cfg, conn, initial_mode="backfill")

    repo = Repo(conn)
    feed = repo.get_feed_by_channel_id(_CHANNEL_ID)
    assert feed is not None
    assert feed.feed_url == playlist.feed_url_for(_PLAYLIST_ID)
    assert feed.channel_title == "Meine Playlist"

    assert pl_patched["list_calls"] == 1
    for video_id in ("rss00000001", "rss00000002", "old00000001", "old00000002"):
        assert repo.get_video(video_id).status == "discovered"
    # Datum und Dauer der Zusatz-Videos kommen aus der Probe
    old = repo.get_video("old00000001")
    assert old.published_at.startswith("2024-03-01")
    assert old.duration_s == 900
    assert report.new_videos == 4


def test_unavailable_entries_are_not_probed_or_stored(cfg, conn, pl_patched):
    _write_feeds(cfg)
    _sync(cfg, conn, initial_mode="backfill")

    assert "priv0000001" not in pl_patched["probed"]
    assert not Repo(conn).video_exists("priv0000001")
    # RSS-Einträge haben schon ein Datum - nur die Zusatz-Videos werden geprobt
    assert sorted(pl_patched["probed"]) == ["old00000001", "old00000002"]


def test_first_sync_respects_initial_mode_for_extra_videos(cfg, conn, pl_patched):
    _write_feeds(cfg)
    _sync(cfg, conn, initial_mode="mark-seen")

    repo = Repo(conn)
    for video_id in ("rss00000001", "old00000001", "old00000002"):
        video = repo.get_video(video_id)
        assert video.status == "skipped"
        assert video.skip_reason == "initial_sync"


def test_later_sync_with_known_rss_entry_skips_complete_list(cfg, conn, pl_patched):
    _write_feeds(cfg)
    _sync(cfg, conn, initial_mode="backfill")
    pl_patched["list_calls"] = 0
    pl_patched["probed"].clear()

    # neues Video im RSS, ein älteres ist schon bekannt -> nur einfacher Abgleich
    pl_patched["rss"].insert(0, _rss_entry("rss00000003", 25))
    report = _sync(cfg, conn)

    assert pl_patched["list_calls"] == 0
    assert pl_patched["probed"] == []
    assert Repo(conn).get_video("rss00000003").status == "discovered"
    assert report.new_videos == 1


def test_gap_after_many_new_videos_triggers_complete_list(cfg, conn, pl_patched):
    _write_feeds(cfg)
    _sync(cfg, conn, initial_mode="backfill")
    pl_patched["list_calls"] = 0

    # seit dem letzten Sync mehr neue Videos, als der RSS-Feed fasst: im RSS
    # steht nur noch Unbekanntes, ein weiteres liegt nur in der Gesamtliste
    pl_patched["rss"] = [_rss_entry("new00000001", 28), _rss_entry("new00000002", 27)]
    pl_patched["listed"] = [
        {"video_id": "new00000001", "title": "Titel new00000001"},
        {"video_id": "new00000002", "title": "Titel new00000002"},
        {"video_id": "gap00000001", "title": "Lücke"},
        {"video_id": "rss00000001", "title": "Titel rss00000001"},
    ]
    pl_patched["dates"]["gap00000001"] = "20260926"
    report = _sync(cfg, conn)

    assert pl_patched["list_calls"] == 1
    repo = Repo(conn)
    for video_id in ("new00000001", "new00000002", "gap00000001"):
        assert repo.get_video(video_id).status == "discovered"
    assert report.new_videos == 3


def test_failed_complete_list_stores_nothing_and_retries_next_sync(cfg, conn, pl_patched):
    _write_feeds(cfg)
    pl_patched["list_error"] = "HTTP Error 429"
    report = _sync(cfg, conn, initial_mode="mark-seen")

    repo = Repo(conn)
    feed = repo.get_feed_by_channel_id(_CHANNEL_ID)
    assert feed.last_error.startswith("playlist_list:")
    assert "429" in feed.last_error
    assert report.feeds_failed == 1
    assert not repo.video_exists("rss00000001")

    # nächster Sync klappt: weiterhin Erstlauf (Feed noch leer) -> mark-seen greift
    pl_patched["list_error"] = None
    _sync(cfg, conn, initial_mode="mark-seen")
    assert repo.get_video("rss00000001").skip_reason == "initial_sync"
    assert repo.get_video("old00000001").skip_reason == "initial_sync"


def test_empty_rss_does_not_trigger_complete_list(cfg, conn, pl_patched):
    _write_feeds(cfg)
    pl_patched["rss"] = []
    report = _sync(cfg, conn)

    assert pl_patched["list_calls"] == 0
    assert report.new_videos == 0


# ---------------------------------------------------------------- backfill


def _seed_playlist_feed(conn):
    repo = Repo(conn)
    feed = repo.create_feed(channel_id=_CHANNEL_ID, feed_url=playlist.feed_url_for(_PLAYLIST_ID),
                            dir_slug="Playlist", channel_title="Meine Playlist",
                            display_name=None)
    repo.add_video(_rss_entry("rss00000001", 20), feed.id, status="done")
    return feed


def test_backfill_playlist_adds_unknown_and_skips_known_and_unavailable(cfg, conn, pl_patched):
    _seed_playlist_feed(conn)
    report = Pipeline(cfg, conn).backfill_channel(_CHANNEL_ID, days=99999)

    repo = Repo(conn)
    assert repo.get_video("rss00000001").status == "done"  # unverändert
    assert repo.get_video("old00000001").status == "discovered"
    assert repo.get_video("old00000002").status == "discovered"
    assert not repo.video_exists("priv0000001")
    assert "rss00000001" not in pl_patched["probed"]
    assert report.new_videos == 3  # rss00000002 + zwei alte


def test_backfill_playlist_cutoff_skips_old_but_keeps_going(cfg, conn, pl_patched):
    # Playlist ist nicht chronologisch: das alte Video steht VOR dem neuen -
    # der Lauf darf dort nicht abbrechen (anders als beim Kanal-Backfill)
    pl_patched["listed"] = [
        {"video_id": "old00000002", "title": "Alt 2"},
        {"video_id": "rss00000002", "title": "Titel rss00000002"},
    ]
    pl_patched["dates"]["rss00000002"] = "20260919"
    _seed_playlist_feed(conn)
    report = Pipeline(cfg, conn).backfill_channel(_CHANNEL_ID, days=365)

    repo = Repo(conn)
    assert not repo.video_exists("old00000002")
    assert repo.get_video("rss00000002").status == "discovered"
    assert report.new_videos == 1


def test_backfill_playlist_dry_run_and_limit(cfg, conn, pl_patched):
    _seed_playlist_feed(conn)
    dry = Pipeline(cfg, conn).backfill_channel(_CHANNEL_ID, days=99999, dry_run=True)
    assert dry.new_videos == 3
    assert not Repo(conn).video_exists("old00000001")

    limited = Pipeline(cfg, conn).backfill_channel(_CHANNEL_ID, days=99999, limit=1)
    assert limited.new_videos == 1
