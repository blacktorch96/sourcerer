"""HTTP mit Conditional GET, Atom-Parsing, einmalige @handle-Auflösung."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import httpx

from ytdigest.models import FeedEntry

_USER_AGENT = "ytdigest/0.1 (+https://github.com/ ; personal use)"
_CHANNEL_ID_RE = re.compile(r'"(?:channelId|externalId)"\s*:\s*"(UC[0-9A-Za-z_-]{22})"')
_CANONICAL_RE = re.compile(r'youtube\.com/channel/(UC[0-9A-Za-z_-]{22})')


@dataclass(slots=True)
class FeedFetchResult:
    not_modified: bool = False
    entries: list[FeedEntry] = field(default_factory=list)
    channel_id: str | None = None
    channel_title: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None


def _entry_datetime(entry) -> datetime:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed is None:
        return datetime.now(UTC)
    return datetime(
        parsed.tm_year, parsed.tm_mon, parsed.tm_mday,
        parsed.tm_hour, parsed.tm_min, parsed.tm_sec, tzinfo=UTC,
    )


def _normalise_channel_id(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("UC"):
        return value
    if re.fullmatch(r"[0-9A-Za-z_-]{22}", value):  # feedparser verschluckt gelegentlich "UC"
        return f"UC{value}"
    return value


def _parse_atom(body: bytes) -> tuple[str | None, str | None, list[FeedEntry]]:
    doc = feedparser.parse(body)
    channel_title = doc.feed.get("title")
    entries: list[FeedEntry] = []
    entry_channel_id: str | None = None
    for item in doc.entries:
        video_id = item.get("yt_videoid")
        if not video_id:
            continue
        entry_channel_id = entry_channel_id or _normalise_channel_id(item.get("yt_channelid"))
        entries.append(
            FeedEntry(
                video_id=video_id,
                title=item.get("title", "").strip() or video_id,
                published_at=_entry_datetime(item),
                url=item.get("link") or f"https://www.youtube.com/watch?v={video_id}",
                channel_id=_normalise_channel_id(item.get("yt_channelid")),
                channel_title=channel_title,
            )
        )
    channel_id = entry_channel_id or _normalise_channel_id(doc.feed.get("yt_channelid"))
    return channel_id, channel_title, entries


def fetch_feed(feed_url: str, *, timeout_s: float, etag: str | None = None,
               last_modified: str | None = None,
               conditional: bool = True) -> FeedFetchResult:
    headers = {"User-Agent": _USER_AGENT}
    if conditional and etag:
        headers["If-None-Match"] = etag
    if conditional and last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        resp = httpx.get(feed_url, headers=headers, timeout=timeout_s,
                         follow_redirects=True)
    except httpx.HTTPError as exc:
        return FeedFetchResult(error=f"http_error: {exc}")

    if resp.status_code == 304:
        return FeedFetchResult(not_modified=True, etag=etag, last_modified=last_modified)
    if resp.status_code in (403, 429):
        return FeedFetchResult(error=f"rate_limited ({resp.status_code})")
    if resp.status_code >= 400:
        return FeedFetchResult(error=f"http_{resp.status_code}")

    channel_id, channel_title, entries = _parse_atom(resp.content)
    return FeedFetchResult(
        entries=entries,
        channel_id=channel_id,
        channel_title=channel_title,
        etag=resp.headers.get("ETag"),
        last_modified=resp.headers.get("Last-Modified"),
    )


def resolve_handle(handle_or_url: str, *, timeout_s: float) -> str:
    """@handle / Kanal-URL einmalig zur UC-ID auflösen (spec 4.1)."""
    if handle_or_url.startswith("@"):
        url = f"https://www.youtube.com/{handle_or_url}"
    elif handle_or_url.startswith("http"):
        url = handle_or_url
    else:
        url = f"https://www.youtube.com/@{handle_or_url}"

    resp = httpx.get(url, headers={"User-Agent": _USER_AGENT},
                     timeout=timeout_s, follow_redirects=True)
    resp.raise_for_status()
    text = resp.text
    for pattern in (_CANONICAL_RE, _CHANNEL_ID_RE):
        match = pattern.search(text)
        if match:
            return match.group(1)
    raise ValueError(f"Kanal-ID nicht auflösbar: {handle_or_url}")
