"""feeds.txt lesen und Zeilen normalisieren (spec 4.1)."""

from __future__ import annotations

import re
from pathlib import Path

from ytdigest.models import ParsedFeedLine
from ytdigest.sources import local, local_remote, playlist

CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
_VIDEOS_XML_RE = re.compile(r"youtube\.com/feeds/videos\.xml", re.IGNORECASE)
_CHANNEL_ID_IN_URL = re.compile(r"[?&]channel_id=(UC[0-9A-Za-z_-]{22})")
_HANDLE_RE = re.compile(r"^@[\w.-]+$")
_PODCAST_PREFIX_RE = re.compile(r"^podcast:\s*", re.IGNORECASE)
_LOCAL_PREFIX_RE = re.compile(r"^local:\s*", re.IGNORECASE)
_PLAYLIST_PREFIX_RE = re.compile(r"^playlist:\s*", re.IGNORECASE)
_PLAYLIST_ID_RE = re.compile(r"^[0-9A-Za-z_-]{10,}$")  # PL..., UU..., OLAK5uy_... usw.
_PLAYLIST_ID_IN_URL = re.compile(r"[?&](?:list|playlist_id)=([0-9A-Za-z_-]+)")
_PLAYLIST_URL_RE = re.compile(
    r"youtube\.com/playlist\?|youtube\.com/feeds/videos\.xml\?[^ ]*playlist_id=",
    re.IGNORECASE,
)


def _playlist_id_from(ref: str) -> str | None:
    """Playlist-ID aus einer nackten ID oder einer URL mit ``list=``/``playlist_id=``."""
    if _PLAYLIST_ID_RE.match(ref):
        return ref
    match = _PLAYLIST_ID_IN_URL.search(ref)
    return match.group(1) if match else None


def feed_url_for_channel(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def parse_line(raw: str) -> ParsedFeedLine | None:
    """Eine Rohzeile zu einem ParsedFeedLine. None für Kommentare/Leerzeilen."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None

    display_name: str | None = None
    if "|" in line:
        left, _, right = line.partition("|")
        line = left.strip()
        display_name = right.strip() or None

    if _PODCAST_PREFIX_RE.match(line):
        url = _PODCAST_PREFIX_RE.sub("", line, count=1).strip()
        if not url:
            raise ValueError(f"Podcast-Zeile ohne URL: {raw!r}")
        return ParsedFeedLine(raw=raw, podcast_url=url, display_name=display_name)

    if _LOCAL_PREFIX_RE.match(line):
        path_str = _LOCAL_PREFIX_RE.sub("", line, count=1).strip()
        if not path_str:
            raise ValueError(f"local:-Zeile ohne Pfad: {raw!r}")
        if path_str.startswith(("http://", "https://")):
            return ParsedFeedLine(raw=raw, local_remote_url=path_str.rstrip("/"),
                                  display_name=display_name)
        return ParsedFeedLine(raw=raw, local_dir=local.parse_local_path(path_str),
                              display_name=display_name)

    if _PLAYLIST_PREFIX_RE.match(line):
        ref = _PLAYLIST_PREFIX_RE.sub("", line, count=1).strip()
        playlist_id = _playlist_id_from(ref)
        if not playlist_id:
            raise ValueError(f"Playlist-Zeile ohne gültige Playlist-ID/-URL: {raw!r}")
        return ParsedFeedLine(raw=raw, playlist_id=playlist_id, display_name=display_name)

    # vor dem videos.xml-Zweig: dessen playlist_id-Variante hat keine Kanal-ID
    if _PLAYLIST_URL_RE.search(line):
        playlist_id = _playlist_id_from(line)
        if not playlist_id:
            raise ValueError(f"Playlist-URL ohne list=-Parameter: {raw!r}")
        return ParsedFeedLine(raw=raw, playlist_id=playlist_id, display_name=display_name)

    if _VIDEOS_XML_RE.search(line):
        match = _CHANNEL_ID_IN_URL.search(line)
        return ParsedFeedLine(
            raw=raw, feed_url=line,
            channel_id=match.group(1) if match else None,
            display_name=display_name,
        )

    if CHANNEL_ID_RE.match(line):
        return ParsedFeedLine(
            raw=raw, channel_id=line, feed_url=feed_url_for_channel(line),
            display_name=display_name,
        )

    channel_match = re.search(r"youtube\.com/channel/(UC[0-9A-Za-z_-]{22})", line)
    if channel_match:
        cid = channel_match.group(1)
        return ParsedFeedLine(
            raw=raw, channel_id=cid, feed_url=feed_url_for_channel(cid),
            display_name=display_name,
        )

    if _HANDLE_RE.match(line):
        return ParsedFeedLine(raw=raw, handle=line, display_name=display_name)

    if "youtube.com/" in line:  # @handle-, /c/-, /user/-URL
        return ParsedFeedLine(raw=raw, handle=line, display_name=display_name)

    raise ValueError(f"Unverständliche feeds.txt-Zeile: {raw!r}")


def append_line(path: Path, line: str) -> None:
    """Zeile an feeds.txt anhängen (spiegelt ``cli.feeds_add``, für die Web-UI)."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(line + "\n")


def _line_matches_feed(parsed: ParsedFeedLine, feed) -> bool:
    """``feed.resolved_from`` ist der einmal aufgelöste Ursprungstoken (z. B.
    '@handle') - nur darüber lässt sich eine Handle-Zeile eindeutig einem
    bereits bekannten Feed zuordnen, da die Zeile selbst keine Kanal-ID trägt."""
    if parsed.local_dir:
        return feed.feed_url == local.feed_url_for(parsed.local_dir)
    if parsed.local_remote_url:
        return feed.feed_url == local_remote.feed_url_for(parsed.local_remote_url)
    if parsed.playlist_id:
        return feed.channel_id == playlist.channel_id_for(parsed.playlist_id)
    if feed.resolved_from:
        return parsed.handle == feed.resolved_from
    if parsed.podcast_url:
        return parsed.podcast_url == feed.feed_url
    return parsed.channel_id == feed.channel_id or parsed.feed_url == feed.feed_url


def remove_line(path: Path, feed) -> bool:
    """Die feeds.txt-Zeile entfernen, die zu ``feed`` aufgelöst wurde (siehe
    ``_line_matches_feed``). Andere Zeilen (inkl. Kommentare/Leerzeilen)
    bleiben unverändert. Gibt True zurück, wenn eine Zeile entfernt wurde."""
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed = False
    for raw in lines:
        try:
            parsed = parse_line(raw)
        except ValueError:
            parsed = None
        if not removed and parsed is not None and _line_matches_feed(parsed, feed):
            removed = True
            continue
        kept.append(raw)
    if removed:
        path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8", newline="\n")
    return removed


def parse_feeds_file(path: Path) -> list[ParsedFeedLine]:
    if not path.is_file():
        return []
    out: list[ParsedFeedLine] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_line(raw)
        if parsed is None:
            continue
        key = (parsed.feed_url or parsed.channel_id or parsed.handle
               or parsed.podcast_url or parsed.playlist_id
               or (str(parsed.local_dir) if parsed.local_dir else None)
               or parsed.local_remote_url
               or parsed.raw)
        if key in seen:
            continue
        seen.add(key)
        out.append(parsed)
    return out
