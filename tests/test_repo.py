from datetime import UTC, datetime

from ytdigest.db.repo import Repo
from ytdigest.models import FeedEntry, TranscriptResult


def _entry(video_id="vid00000001", published=None):
    return FeedEntry(
        video_id=video_id,
        title="Ein Titel",
        published_at=published or datetime(2026, 8, 30, tzinfo=UTC),
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _feed(repo: Repo):
    return repo.create_feed(
        channel_id="UCbRP3c757lWg9M-U7TyEkXA",
        feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCbRP3c757lWg9M-U7TyEkXA",
        dir_slug="Theo", channel_title="Theo", display_name=None,
    )


def test_processing_reset_on_start(conn):
    repo = Repo(conn)
    feed = _feed(repo)
    repo.add_video(_entry(), feed.id, status="discovered")
    repo.mark_processing("vid00000001")
    assert repo.get_video("vid00000001").status == "processing"

    assert repo.reset_processing() == 1
    assert repo.get_video("vid00000001").status == "discovered"


def test_claim_respects_max_attempts_and_retry_time(conn):
    repo = Repo(conn)
    feed = _feed(repo)
    repo.add_video(_entry("vid00000001"), feed.id, status="discovered")
    repo.add_video(_entry("vid00000002"), feed.id, status="discovered")

    repo.conn.execute(
        "UPDATE videos SET status='failed', attempts=3, next_retry_at=NULL "
        "WHERE video_id='vid00000002'"
    )
    claimed = {v.video_id for v in repo.claim_videos(max_attempts=3)}
    assert claimed == {"vid00000001"}


def test_finish_done_sets_paths_and_counts(conn):
    repo = Repo(conn)
    feed = _feed(repo)
    repo.add_video(_entry(), feed.id, status="discovered")
    repo.mark_processing("vid00000001")
    result = TranscriptResult(text="hallo welt", source="captions_auto", language="de")
    repo.finish_done("vid00000001", result, transcript_path="Theo/x.txt",
                     metadata_path="Theo/x.json")
    video = repo.get_video("vid00000001")
    assert video.status == "done"
    assert video.transcript_path == "Theo/x.txt"
    assert video.word_count == 2


def test_recent_done_orders_by_processed_at_desc(conn):
    repo = Repo(conn)
    feed = _feed(repo)
    result = TranscriptResult(text="hallo welt", source="captions_auto", language="de")
    for video_id, processed in (
        ("vid00000001", "2026-08-01T00:00:00Z"),
        ("vid00000002", "2026-08-03T00:00:00Z"),
        ("vid00000003", "2026-08-02T00:00:00Z"),
    ):
        repo.add_video(_entry(video_id), feed.id, status="discovered")
        repo.mark_processing(video_id)
        repo.finish_done(video_id, result, transcript_path=f"Theo/{video_id}.txt",
                         metadata_path=f"Theo/{video_id}.json")
        repo.conn.execute("UPDATE videos SET processed_at = ? WHERE video_id = ?",
                          (processed, video_id))

    recent = repo.recent_done(limit=10)
    assert [v.video_id for v in recent] == ["vid00000002", "vid00000003", "vid00000001"]


def test_recent_done_respects_limit(conn):
    repo = Repo(conn)
    feed = _feed(repo)
    result = TranscriptResult(text="hallo welt", source="captions_auto", language="de")
    for i in range(3):
        video_id = f"vid0000000{i}"
        repo.add_video(_entry(video_id), feed.id, status="discovered")
        repo.mark_processing(video_id)
        repo.finish_done(video_id, result, transcript_path=f"Theo/{video_id}.txt",
                         metadata_path=f"Theo/{video_id}.json")

    assert len(repo.recent_done(limit=2)) == 2


def test_recent_done_excludes_non_done_videos(conn):
    repo = Repo(conn)
    feed = _feed(repo)
    repo.add_video(_entry(), feed.id, status="discovered")
    assert repo.recent_done() == []


def test_requeue_failed(conn):
    repo = Repo(conn)
    feed = _feed(repo)
    repo.add_video(_entry(), feed.id, status="discovered")
    repo.conn.execute("UPDATE videos SET status='failed', attempts=3")
    assert repo.requeue_failed() == 1
    assert repo.get_video("vid00000001").status == "discovered"


def test_deactivate_missing_feeds(conn):
    repo = Repo(conn)
    _feed(repo)
    assert repo.deactivate_missing_feeds({"UCother"}) == 1
    assert repo.list_feeds()[0].is_active is False


def test_list_feeds_sorts_case_insensitively(conn):
    repo = Repo(conn)
    # 'todocast' beginnt klein, müsste bei reiner Byte-Sortierung (Großbuchstaben
    # vor Kleinbuchstaben) hinter allen groß geschriebenen Slugs landen -
    # case-insensitiv gehört es alphabetisch zwischen 'Alex' und 'Becoming'
    repo.create_feed(channel_id="UCa", feed_url="https://a", dir_slug="Becoming_CTO",
                     channel_title="Becoming CTO", display_name=None)
    repo.create_feed(channel_id="UCb", feed_url="https://b", dir_slug="todocast_Podcast",
                     channel_title="todocast Podcast", display_name=None)
    repo.create_feed(channel_id="UCc", feed_url="https://c", dir_slug="Alex_Ziskind",
                     channel_title="Alex Ziskind", display_name=None)

    slugs = [f.dir_slug for f in repo.list_feeds()]
    assert slugs == ["Alex_Ziskind", "Becoming_CTO", "todocast_Podcast"]
