import pytest

from ytdigest.feeds.parser import parse_feeds_file, parse_line


def test_comments_and_blank_lines_ignored():
    assert parse_line("# Kommentar") is None
    assert parse_line("   ") is None


def test_channel_id_expands_to_feed_url():
    parsed = parse_line("UCbRP3c757lWg9M-U7TyEkXA")
    assert parsed.channel_id == "UCbRP3c757lWg9M-U7TyEkXA"
    assert parsed.feed_url.endswith("channel_id=UCbRP3c757lWg9M-U7TyEkXA")


def test_full_videos_xml_url_kept_and_channel_id_extracted():
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCbRP3c757lWg9M-U7TyEkXA"
    parsed = parse_line(url)
    assert parsed.feed_url == url
    assert parsed.channel_id == "UCbRP3c757lWg9M-U7TyEkXA"


def test_handle_needs_resolution():
    parsed = parse_line("@t3dotgg")
    assert parsed.handle == "@t3dotgg"
    assert parsed.channel_id is None


def test_display_name_override():
    parsed = parse_line("@t3dotgg | Theo")
    assert parsed.handle == "@t3dotgg"
    assert parsed.display_name == "Theo"


def test_unparseable_line_raises():
    with pytest.raises(ValueError):
        parse_line("not a feed at all")


def test_podcast_prefix_parsed():
    parsed = parse_line("podcast:https://rss.buzzsprout.com/2402174.rss | Mein Podcast")
    assert parsed.podcast_url == "https://rss.buzzsprout.com/2402174.rss"
    assert parsed.display_name == "Mein Podcast"
    assert parsed.channel_id is None
    assert parsed.handle is None


def test_podcast_prefix_case_insensitive_and_needs_url():
    parsed = parse_line("PODCAST:https://example.com/feed.rss")
    assert parsed.podcast_url == "https://example.com/feed.rss"
    with pytest.raises(ValueError):
        parse_line("podcast:")


def test_file_dedupes(tmp_path):
    f = tmp_path / "feeds.txt"
    f.write_text(
        "# eins\n"
        "UCbRP3c757lWg9M-U7TyEkXA\n"
        "UCbRP3c757lWg9M-U7TyEkXA\n"
        "@theo | Theo\n",
        encoding="utf-8",
    )
    parsed = parse_feeds_file(f)
    assert len(parsed) == 2
