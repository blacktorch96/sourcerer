"""yt-dlp: flache Kanal-Videoliste jenseits des RSS-Fensters (max. ~15 Einträge).

Der YouTube-Atom-Feed liefert nur die letzten Uploads - für einen Backfill über
Monate zurück reicht das nicht. ``list_uploads`` fragt stattdessen den
"Videos"-Tab des Kanals flach ab (schnell, aber ohne Datum je Video); das
Datum muss der Aufrufer je Kandidat per ``sources.captions.probe`` nachholen.
"""

from __future__ import annotations

_YDL_FLAT_OPTS = {
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "extract_flat": "in_playlist",
}


def list_uploads(channel_id: str) -> list[dict]:
    """Video-IDs und Titel eines Kanals, neueste zuerst."""
    from yt_dlp import YoutubeDL

    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    with YoutubeDL(_YDL_FLAT_OPTS) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") or []
    return [
        {"video_id": e["id"], "title": (e.get("title") or "").strip()}
        for e in entries if e.get("id")
    ]
