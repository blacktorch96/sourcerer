from datetime import UTC, datetime

import pytest

from ytdigest.naming import build_basename, slugify


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Über Grüße für Öl", "Ueber_Gruesse_fuer_Oel"),
        ('a<b>c:"d/e\\f|g?h*i', "abcdefghi"),
        ("  ...trim._me...  ", "trim._me"),
        ("multiple     spaces", "multiple_spaces"),
        ("straße", "strasse"),
    ],
)
def test_slugify_rules(raw, expected):
    assert slugify(raw) == expected


def test_reserved_windows_names_get_suffixed():
    assert slugify("CON") == "CON_"
    assert slugify("com1").upper() == "COM1_"


def test_overlength_cut_at_word_boundary():
    text = "wort " * 60
    out = slugify(text, max_len=20)
    assert len(out) <= 20
    assert not out.endswith("_")


def test_empty_result_falls_back_to_video_id():
    published = datetime(2026, 8, 30, 14, 30, 12, tzinfo=UTC)
    base = build_basename(published, "***", video_id="dQw4w9WgXcQ")
    assert base == "2026-08-30_143012_dQw4w9WgXcQ"


def test_force_id_appends():
    published = datetime(2026, 8, 30, 14, 30, 12, tzinfo=UTC)
    base = build_basename(published, "Titel", video_id="abc", force_id=True)
    assert base.endswith("_abc")
