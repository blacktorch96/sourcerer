from ytdigest.feeds.fetcher import _normalise_channel_id, _parse_atom


def test_normalise_channel_id_restores_uc_prefix():
    assert _normalise_channel_id("XuqSBlHAE6Xw-yeJA0Tunw") == "UCXuqSBlHAE6Xw-yeJA0Tunw"
    assert _normalise_channel_id("UCXuqSBlHAE6Xw-yeJA0Tunw") == "UCXuqSBlHAE6Xw-yeJA0Tunw"
    assert _normalise_channel_id(None) is None


def test_parse_atom_fixture(fixtures):
    body = (fixtures / "feed_basic.xml").read_bytes()
    channel_id, title, entries = _parse_atom(body)
    assert channel_id == "UCbRP3c757lWg9M-U7TyEkXA"
    assert title == "Theo - t3.gg"
    assert [e.video_id for e in entries] == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    assert entries[0].published_at.year == 2026
