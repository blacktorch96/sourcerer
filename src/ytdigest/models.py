"""Reine Datencontainer, ohne Verhalten und ohne I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ParsedFeedLine:
    """Eine normalisierte Zeile aus feeds.txt."""

    raw: str
    channel_id: str | None = None      # gesetzt, wenn direkt bekannt
    feed_url: str | None = None         # gesetzt, wenn direkt bekannt
    handle: str | None = None           # '@name' oder Kanal-URL, muss aufgelöst werden
    podcast_url: str | None = None      # 'podcast:<url>'-Zeile: normale Podcast-RSS
    display_name: str | None = None     # Override für den Verzeichnisnamen


@dataclass(slots=True)
class Feed:
    id: int
    channel_id: str
    feed_url: str
    dir_slug: str
    channel_title: str | None = None
    display_name: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    last_checked_at: str | None = None
    last_error: str | None = None
    is_active: bool = True

    @property
    def name(self) -> str:
        return self.display_name or self.channel_title or self.channel_id


@dataclass(slots=True)
class FeedEntry:
    """Ein Video-Eintrag, wie er aus dem Atom-Feed kommt."""

    video_id: str
    title: str
    published_at: datetime
    url: str
    channel_id: str | None = None
    channel_title: str | None = None
    duration_s: int | None = None       # nur gesetzt, wenn schon im Feed bekannt
                                         # (z. B. itunes:duration)


@dataclass(slots=True)
class Video:
    video_id: str
    feed_id: int
    title: str
    published_at: str
    url: str
    status: str
    duration_s: int | None = None
    source: str | None = None
    language: str | None = None
    transcript_path: str | None = None
    metadata_path: str | None = None
    char_count: int | None = None
    word_count: int | None = None
    attempts: int = 0
    last_error: str | None = None
    skip_reason: str | None = None
    next_retry_at: str | None = None


@dataclass(slots=True)
class VideoProbe:
    """Ergebnis des yt-dlp-Metadatenabrufs."""

    duration_s: int | None
    manual_langs: list[str] = field(default_factory=list)
    auto_langs: list[str] = field(default_factory=list)
    original_lang: str | None = None    # tatsächliche Sprachspur des Videos
    error: str | None = None            # kurze Klartextursache, siehe spec 10
    info: dict = field(default_factory=dict)


@dataclass(slots=True)
class TranscriptResult:
    text: str
    source: str                         # captions_manual | captions_auto | asr
    language: str
    asr_model: str | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass(slots=True)
class RunReport:
    feeds_checked: int = 0
    feeds_failed: int = 0
    new_videos: int = 0
    processed: int = 0
    via_captions: int = 0
    via_asr: int = 0
    no_transcript: int = 0
    skipped: int = 0
    failed: int = 0
    runtime_s: float = 0.0

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0
