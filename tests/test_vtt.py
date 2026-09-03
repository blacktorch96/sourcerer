from ytdigest.sources.vtt import vtt_to_text


def test_normalises_auto_captions(fixtures):
    raw = (fixtures / "captions_auto.vtt").read_text(encoding="utf-8")
    out = vtt_to_text(raw)
    assert "WEBVTT" not in out
    assert "-->" not in out
    assert "<c>" not in out
    assert "00:00:01" not in out
    lines = out.splitlines()
    assert lines == ["hello and welcome", "to the show"]


def test_empty_input():
    assert vtt_to_text("") == ""
    assert vtt_to_text("WEBVTT\n\n") == ""
