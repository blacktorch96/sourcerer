from ytdigest.feeds.podcast_fetcher import _parse_itunes_duration, _parse_podcast_rss


def test_parse_itunes_duration_variants():
    assert _parse_itunes_duration("00:42:15") == 42 * 60 + 15
    assert _parse_itunes_duration("25:00") == 25 * 60
    assert _parse_itunes_duration("1500") == 1500
    assert _parse_itunes_duration(None) is None
    assert _parse_itunes_duration("") is None
    assert _parse_itunes_duration("nicht lesbar") is None


def test_parse_podcast_rss_fixture(fixtures):
    body = (fixtures / "podcast_basic.xml").read_bytes()
    title, entries = _parse_podcast_rss(body, channel_id="podcast:https://example.com/feed.rss")

    assert title == "Mein Test-Podcast"
    assert len(entries) == 2
    newest, oldest = entries  # Reihenfolge wie im Feed, kein Sortieren hier
    assert newest.title == "Folge 2: Die zweite"
    assert newest.url == "https://cdn.example.com/audio/episode2.mp3"
    assert newest.duration_s == 42 * 60 + 15
    assert newest.video_id.startswith("podcast:https://example.com/feed.rss/")
    assert oldest.duration_s == 1500
    # gleiche guid -> gleiche video_id (stabile Identität über Läufe hinweg)
    _, entries_again = _parse_podcast_rss(body, channel_id="podcast:https://example.com/feed.rss")
    assert entries_again[0].video_id == newest.video_id
