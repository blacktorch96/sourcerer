from pathlib import Path

import pytest

from ytdigest.feeds.parser import parse_feeds_file, parse_line, remove_line
from ytdigest.sources import local_remote


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


def test_local_prefix_parsed_posix_path():
    parsed = parse_line("local:/home/me/aufnahmen | Meine Aufnahmen")
    assert parsed.local_dir == Path("/home/me/aufnahmen")
    assert parsed.display_name == "Meine Aufnahmen"
    assert parsed.channel_id is None
    assert parsed.handle is None


def test_local_prefix_parsed_windows_path():
    parsed = parse_line(r"local:C:\Users\me\Aufnahmen")
    assert parsed.local_dir == Path("C:/Users/me/Aufnahmen")


def test_local_prefix_case_insensitive_and_needs_path():
    parsed = parse_line("LOCAL:/tmp/aufnahmen")
    assert parsed.local_dir == Path("/tmp/aufnahmen")
    with pytest.raises(ValueError):
        parse_line("local:")


def test_local_prefix_with_http_url_is_remote():
    parsed = parse_line("local:http://192.168.1.50:8765/folders/fom_s4 | FOM S4")
    assert parsed.local_remote_url == "http://192.168.1.50:8765/folders/fom_s4"
    assert parsed.local_dir is None
    assert parsed.display_name == "FOM S4"


def test_local_prefix_with_https_url_is_remote():
    parsed = parse_line("local:https://pc.local:8765/folders/fom_s4")
    assert parsed.local_remote_url == "https://pc.local:8765/folders/fom_s4"


def test_local_prefix_remote_url_trailing_slash_stripped():
    parsed = parse_line("local:http://pc.local:8765/folders/fom_s4/")
    assert parsed.local_remote_url == "http://pc.local:8765/folders/fom_s4"


def test_remove_line_removes_matching_remote_local_feed(tmp_path):
    from ytdigest.models import Feed

    folder_url = "http://192.168.1.50:8765/folders/fom_s4"
    f = tmp_path / "feeds.txt"
    f.write_text(f"local:{folder_url} | FOM S4\n@other\n", encoding="utf-8")
    feed = Feed(id=1, channel_id="fom_s4", feed_url=local_remote.feed_url_for(folder_url),
               dir_slug="fom_s4")

    removed = remove_line(f, feed)

    assert removed is True
    assert "local:" not in f.read_text(encoding="utf-8")
    assert "@other" in f.read_text(encoding="utf-8")


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
