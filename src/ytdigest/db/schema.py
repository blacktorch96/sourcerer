"""DDL und Migrationen. Zugriff über sqlite3 aus der Standardbibliothek."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS feeds (
    id              INTEGER PRIMARY KEY,
    channel_id      TEXT    NOT NULL UNIQUE,
    feed_url        TEXT    NOT NULL UNIQUE,
    channel_title   TEXT,
    display_name    TEXT,
    resolved_from   TEXT,              -- Ursprungszeile (z. B. @handle), Auflösungs-Cache
    dir_slug        TEXT    NOT NULL,
    etag            TEXT,
    last_modified   TEXT,
    added_at        TEXT    NOT NULL,
    last_checked_at TEXT,
    last_error      TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT    PRIMARY KEY,
    feed_id         INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    title           TEXT    NOT NULL,
    published_at    TEXT    NOT NULL,
    url             TEXT    NOT NULL,
    duration_s      INTEGER,
    status          TEXT    NOT NULL,
    source          TEXT,
    language        TEXT,
    transcript_path TEXT,
    metadata_path   TEXT,
    char_count      INTEGER,
    word_count      INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    skip_reason     TEXT,
    next_retry_at   TEXT,
    first_seen_at   TEXT    NOT NULL,
    processed_at    TEXT,
    summary_status  TEXT    NOT NULL DEFAULT 'none'
);

CREATE INDEX IF NOT EXISTS idx_videos_status  ON videos(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_videos_feed    ON videos(feed_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_summary ON videos(summary_status);
"""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return int(row["version"]) if row else 0


def init_db(database: Path) -> sqlite3.Connection:
    conn = connect(database)
    conn.executescript(_DDL)
    if _current_version(conn) < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, _utcnow()),
        )
    return conn
