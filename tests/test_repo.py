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
