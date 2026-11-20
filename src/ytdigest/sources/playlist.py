"""YouTube-Playlists als Feed-Typ.

Die Videos selbst sind normale YouTube-Videos (Captions, sonst ASR - siehe
``pipeline._process_one``); anders ist nur die Videoquelle: der Atom-Feed
``videos.xml?playlist_id=...`` (wie bei Kanälen max. ~15 Einträge) plus bei
Bedarf die komplette Liste per yt-dlp (``sources.channel.list_playlist_videos``).
Analog zu ``podcast.py`` trägt die DB-``channel_id`` ein Präfix, damit sie
eindeutig ist und sich nicht mit einer echten Kanal-ID überschneidet.
"""

from __future__ import annotations

PLAYLIST_CHANNEL_PREFIX = "playlist:"


def channel_id_for(playlist_id: str) -> str:
    return f"{PLAYLIST_CHANNEL_PREFIX}{playlist_id}"


def is_playlist_feed(channel_id: str) -> bool:
    return channel_id.startswith(PLAYLIST_CHANNEL_PREFIX)


def playlist_id_of(channel_id: str) -> str:
    return channel_id[len(PLAYLIST_CHANNEL_PREFIX):]


def feed_url_for(playlist_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
