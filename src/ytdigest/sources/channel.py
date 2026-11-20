"""yt-dlp: flache Video-Liste jenseits des RSS-Fensters (max. ~15 Einträge).

Der YouTube-Atom-Feed liefert nur die letzten Uploads - für einen Backfill über
Monate zurück reicht das nicht. ``list_uploads`` fragt stattdessen den
"Videos"-Tab des Kanals flach ab, ``list_playlist_videos`` eine Playlist (beides
schnell, aber ohne Datum je Video); das Datum muss der Aufrufer je Kandidat per
``sources.captions.probe`` nachholen.
"""

from __future__ import annotations

_YDL_FLAT_OPTS = {
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "extract_flat": "in_playlist",
}


def _list_flat(url: str) -> list[dict]:
    from yt_dlp import YoutubeDL

    with YoutubeDL(_YDL_FLAT_OPTS) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") or []
    return [
        {"video_id": e["id"], "title": (e.get("title") or "").strip()}
        for e in entries if e.get("id")
    ]


def list_uploads(channel_id: str) -> list[dict]:
    """Video-IDs und Titel eines Kanals, neueste zuerst."""
    return _list_flat(f"https://www.youtube.com/channel/{channel_id}/videos")


def list_playlist_videos(playlist_id: str) -> list[dict]:
    """Video-IDs und Titel einer Playlist in Playlist-Reihenfolge (nicht
    zwingend chronologisch). Nicht mehr abrufbare Einträge erscheinen mit dem
    Titel '[Private video]' bzw. '[Deleted video]'."""
    return _list_flat(f"https://www.youtube.com/playlist?list={playlist_id}")
