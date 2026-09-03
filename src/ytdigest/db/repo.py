"""Queries für Feeds und Videos. Kapselt alle Zustandsübergänge aus spec 5.1."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ytdigest.models import Feed, FeedEntry, TranscriptResult, Video

ACTIVE_PROCESS_STATUSES = ("discovered", "failed")


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _feed_from_row(row: sqlite3.Row) -> Feed:
    return Feed(
        id=row["id"],
        channel_id=row["channel_id"],
        feed_url=row["feed_url"],
        dir_slug=row["dir_slug"],
        channel_title=row["channel_title"],
        display_name=row["display_name"],
        etag=row["etag"],
        last_modified=row["last_modified"],
        last_checked_at=row["last_checked_at"],
        last_error=row["last_error"],
        is_active=bool(row["is_active"]),
    )


def _video_from_row(row: sqlite3.Row) -> Video:
    return Video(
        video_id=row["video_id"],
        feed_id=row["feed_id"],
        title=row["title"],
        published_at=row["published_at"],
        url=row["url"],
        status=row["status"],
        duration_s=row["duration_s"],
        source=row["source"],
        language=row["language"],
        transcript_path=row["transcript_path"],
        metadata_path=row["metadata_path"],
        char_count=row["char_count"],
        word_count=row["word_count"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        skip_reason=row["skip_reason"],
        next_retry_at=row["next_retry_at"],
    )


class Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---- Feeds -------------------------------------------------------------

    def get_feed_by_channel_id(self, channel_id: str) -> Feed | None:
        row = self.conn.execute(
            "SELECT * FROM feeds WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return _feed_from_row(row) if row else None

    def get_feed_by_url(self, feed_url: str) -> Feed | None:
        row = self.conn.execute(
            "SELECT * FROM feeds WHERE feed_url = ?", (feed_url,)
        ).fetchone()
        return _feed_from_row(row) if row else None

    def list_feeds(self, *, active_only: bool = False) -> list[Feed]:
        sql = "SELECT * FROM feeds"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY dir_slug"
        return [_feed_from_row(r) for r in self.conn.execute(sql)]

    def get_feed_by_resolved_from(self, token: str) -> Feed | None:
        row = self.conn.execute(
            "SELECT * FROM feeds WHERE resolved_from = ?", (token,)
        ).fetchone()
        return _feed_from_row(row) if row else None

    def create_feed(self, *, channel_id: str, feed_url: str, dir_slug: str,
                    channel_title: str | None, display_name: str | None,
                    resolved_from: str | None = None) -> Feed:
        cur = self.conn.execute(
            """INSERT INTO feeds
               (channel_id, feed_url, channel_title, display_name, resolved_from,
                dir_slug, added_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (channel_id, feed_url, channel_title, display_name, resolved_from,
             dir_slug, _utcnow()),
        )
        row = self.conn.execute(
            "SELECT * FROM feeds WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _feed_from_row(row)

    def reactivate_feed(self, feed_id: int, *, display_name: str | None) -> None:
        self.conn.execute(
            "UPDATE feeds SET is_active = 1, display_name = ?, last_error = NULL "
            "WHERE id = ?",
            (display_name, feed_id),
        )

    def deactivate_missing_feeds(self, keep_channel_ids: set[str]) -> int:
        rows = self.conn.execute(
            "SELECT channel_id FROM feeds WHERE is_active = 1"
        ).fetchall()
        gone = [r["channel_id"] for r in rows if r["channel_id"] not in keep_channel_ids]
        for channel_id in gone:
            self.conn.execute(
                "UPDATE feeds SET is_active = 0 WHERE channel_id = ?", (channel_id,)
            )
        return len(gone)

    def mark_feed_checked(self, feed_id: int, *, etag: str | None,
                          last_modified: str | None, error: str | None = None) -> None:
        self.conn.execute(
            """UPDATE feeds
               SET last_checked_at = ?, etag = ?, last_modified = ?, last_error = ?
               WHERE id = ?""",
            (_utcnow(), etag, last_modified, error, feed_id),
        )

    def set_feed_error(self, feed_id: int, error: str) -> None:
        self.conn.execute(
            "UPDATE feeds SET last_error = ?, last_checked_at = ? WHERE id = ?",
            (error, _utcnow(), feed_id),
        )

    def update_channel_title(self, feed_id: int, title: str) -> None:
        self.conn.execute(
            "UPDATE feeds SET channel_title = ? WHERE id = ?", (title, feed_id)
        )

    # ---- Videos ----------------------------------------------------------

    def get_video(self, video_id: str) -> Video | None:
        row = self.conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return _video_from_row(row) if row else None

    def video_exists(self, video_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone() is not None

    def add_video(self, entry: FeedEntry, feed_id: int, *, status: str,
                  skip_reason: str | None = None,
                  duration_s: int | None = None) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO videos
               (video_id, feed_id, title, published_at, url, duration_s,
                status, skip_reason, attempts, first_seen_at, summary_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'none')""",
            (entry.video_id, feed_id, entry.title, iso_utc(entry.published_at),
             entry.url, duration_s, status, skip_reason, _utcnow()),
        )

    def reset_processing(self) -> int:
        cur = self.conn.execute(
            "UPDATE videos SET status = 'discovered' WHERE status = 'processing'"
        )
        return cur.rowcount

    def claim_videos(self, *, max_attempts: int, limit: int | None = None,
                     feed_ids: list[int] | None = None,
                     since: str | None = None, asr_only: bool = False) -> list[Video]:
        now = _utcnow()
        if asr_only:
            where = "v.status = 'no_transcript'"
            params: list = []
        else:
            where = (
                "(v.status = 'discovered' "
                " OR (v.status = 'failed' AND v.attempts < ? "
                "     AND (v.next_retry_at IS NULL OR v.next_retry_at <= ?)))"
            )
            params = [max_attempts, now]
        if feed_ids:
            where += f" AND v.feed_id IN ({','.join('?' * len(feed_ids))})"
            params.extend(feed_ids)
        if since:
            where += " AND v.published_at >= ?"
            params.append(since)

        sql = (
            f"SELECT v.* FROM videos v WHERE {where} "
            "ORDER BY v.published_at ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_video_from_row(r) for r in self.conn.execute(sql, params)]

    def mark_processing(self, video_id: str) -> None:
        self.conn.execute(
            "UPDATE videos SET status = 'processing', attempts = attempts + 1 "
            "WHERE video_id = ?",
            (video_id,),
        )

    def set_duration(self, video_id: str, duration_s: int | None) -> None:
        self.conn.execute(
            "UPDATE videos SET duration_s = ? WHERE video_id = ?",
            (duration_s, video_id),
        )

    def finish_done(self, video_id: str, result: TranscriptResult, *,
                    transcript_path: str, metadata_path: str) -> None:
        self.conn.execute(
            """UPDATE videos
               SET status = 'done', source = ?, language = ?,
                   transcript_path = ?, metadata_path = ?,
                   char_count = ?, word_count = ?, last_error = NULL,
                   next_retry_at = NULL, processed_at = ?
               WHERE video_id = ?""",
            (result.source, result.language, transcript_path, metadata_path,
             result.char_count, result.word_count, _utcnow(), video_id),
        )

    def fail_video(self, video_id: str, error: str, *, next_retry_at: str | None) -> None:
        self.conn.execute(
            "UPDATE videos SET status = 'failed', last_error = ?, next_retry_at = ? "
            "WHERE video_id = ?",
            (error, next_retry_at, video_id),
        )

    def skip_video(self, video_id: str, reason: str, *,
                   duration_s: int | None = None) -> None:
        self.conn.execute(
            "UPDATE videos SET status = 'skipped', skip_reason = ?, "
            "duration_s = COALESCE(?, duration_s) WHERE video_id = ?",
            (reason, duration_s, video_id),
        )

    def set_no_transcript(self, video_id: str) -> None:
        self.conn.execute(
            "UPDATE videos SET status = 'no_transcript', last_error = NULL, "
            "next_retry_at = NULL WHERE video_id = ?",
            (video_id,),
        )

    # ---- Auswertung / retry --------------------------------------------

    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM videos GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def video_counts_per_feed(self) -> dict[int, int]:
        rows = self.conn.execute(
            "SELECT feed_id, COUNT(*) AS n FROM videos GROUP BY feed_id"
        ).fetchall()
        return {r["feed_id"]: r["n"] for r in rows}

    def recent_errors(self, limit: int = 10) -> list[Video]:
        rows = self.conn.execute(
            "SELECT * FROM videos WHERE last_error IS NOT NULL "
            "ORDER BY processed_at DESC, first_seen_at DESC LIMIT ?",
            (limit,),
        )
        return [_video_from_row(r) for r in rows]

    def requeue_failed(self, *, include_no_transcript: bool = False,
                       feed_ids: list[int] | None = None) -> int:
        statuses = ["failed"]
        if include_no_transcript:
            statuses.append("no_transcript")
        placeholders = ",".join("?" * len(statuses))
        sql = (
            f"UPDATE videos SET status = 'discovered', attempts = 0, "
            f"next_retry_at = NULL, last_error = NULL WHERE status IN ({placeholders})"
        )
        params: list = list(statuses)
        if feed_ids:
            sql += f" AND feed_id IN ({','.join('?' * len(feed_ids))})"
            params.extend(feed_ids)
        return self.conn.execute(sql, params).rowcount
