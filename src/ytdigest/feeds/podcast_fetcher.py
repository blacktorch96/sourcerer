"""Standard-Podcast-RSS (RSS 2.0 + itunes-Namespace), kein YouTube-Atom.

Episoden stecken als ``<enclosure>`` (Audio-Datei) in jedem ``<item>`` statt
als ``yt_videoid``. Dauer kommt, falls vorhanden, aus ``<itunes:duration>`` -
damit ist beim Sync kein zusätzlicher Netzwerkzugriff nötig, um zu kurze
Folgen herauszufiltern (siehe ``sources/podcast.py::probe``).
"""

from __future__ import annotations

import hashlib
import re

import feedparser

from ytdigest.feeds.fetcher import FeedFetchResult, entry_datetime, get_conditional
from ytdigest.models import FeedEntry

_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav")
_DURATION_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})$")


def _parse_itunes_duration(value: str | None) -> int | None:
    """'HH:MM:SS', 'MM:SS' oder reine Sekunden -> Sekunden. None bei Unbekanntem."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    match = _DURATION_RE.match(value)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)


def _audio_enclosure(entry) -> str | None:
    for enc in entry.get("enclosures", []):
        href = enc.get("href") or enc.get("url")
        if not href:
            continue
        enc_type = (enc.get("type") or "").lower()
        if enc_type.startswith("audio/") or href.lower().split("?")[0].endswith(_AUDIO_EXTENSIONS):
            return href
    return None


def _parse_podcast_rss(body: bytes, channel_id: str) -> tuple[str | None, list[FeedEntry]]:
    doc = feedparser.parse(body)
    channel_title = doc.feed.get("title")
    entries: list[FeedEntry] = []
    for item in doc.entries:
        audio_url = _audio_enclosure(item)
        if not audio_url:
            continue
        guid = item.get("id") or audio_url
        episode_id = hashlib.sha1(guid.encode("utf-8")).hexdigest()[:16]
        entries.append(
            FeedEntry(
                video_id=f"{channel_id}/{episode_id}",
                title=item.get("title", "").strip() or episode_id,
                published_at=entry_datetime(item),
                url=audio_url,
                channel_id=channel_id,
                channel_title=channel_title,
                duration_s=_parse_itunes_duration(item.get("itunes_duration")),
            )
        )
    return channel_title, entries


def fetch_podcast_feed(feed_url: str, *, channel_id: str, timeout_s: float,
                       etag: str | None = None, last_modified: str | None = None,
                       conditional: bool = True) -> FeedFetchResult:
    resp, error = get_conditional(feed_url, timeout_s=timeout_s, etag=etag,
                                  last_modified=last_modified, conditional=conditional)
    if error is not None:
        return error
    assert resp is not None  # get_conditional: error is None <=> resp gesetzt

    channel_title, entries = _parse_podcast_rss(resp.content, channel_id)
    return FeedFetchResult(
        entries=entries,
        channel_id=channel_id,
        channel_title=channel_title,
        etag=resp.headers.get("ETag"),
        last_modified=resp.headers.get("Last-Modified"),
    )
