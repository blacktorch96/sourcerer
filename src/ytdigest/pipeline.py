"""Orchestrierung von Sync und Verarbeitung.

Kennt die Reihenfolge, aber nichts über yt-dlp oder VTT im Detail. Die Quellen
in ``sources/*`` liefern ein ``TranscriptResult`` oder ``None``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ytdigest.config import Config
from ytdigest.db.repo import Repo, iso_utc
from ytdigest.feeds.fetcher import fetch_feed, resolve_handle
from ytdigest.feeds.parser import feed_url_for_channel, parse_feeds_file
from ytdigest.gdrive import DriveUploader, GDriveUnavailable
from ytdigest.models import Feed, FeedEntry, RunReport, Video
from ytdigest.naming import slugify
from ytdigest.sources import asr, captions
from ytdigest.storage import write_transcript

log = logging.getLogger(__name__)

INITIAL_MODES = ("latest", "backfill", "mark-seen")
_BACKFILL_MAX = 15
_LATEST_PROBE_MAX = 5


@dataclass(slots=True)
class RunOptions:
    initial_mode: str = "latest"
    min_duration_min: int | None = None
    limit: int | None = None
    feed_channel_ids: list[str] = field(default_factory=list)
    since: str | None = None
    no_asr: bool = False
    asr_only: bool = False
    sync_only: bool = False
    dry_run: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


class Pipeline:
    def __init__(self, cfg: Config, conn: sqlite3.Connection) -> None:
        self.cfg = cfg
        self.conn = conn
        self.repo = Repo(conn)
        self._drive: DriveUploader | None = None
        self._drive_failed = False

    # ------------------------------------------------------------------ run
    def run(self, opts: RunOptions) -> RunReport:
        report = RunReport()
        started = time.monotonic()

        reset = self.repo.reset_processing()
        if reset:
            log.info("%d Video(s) aus 'processing' auf 'discovered' zurückgesetzt", reset)

        if not opts.asr_only:
            self.sync(opts, report)
        if not opts.sync_only:
            self.process(opts, report)

        report.runtime_s = time.monotonic() - started
        return report

    # ----------------------------------------------------------------- sync
    def sync(self, opts: RunOptions, report: RunReport) -> None:
        parsed = parse_feeds_file(self.cfg.paths.feeds_file)
        keep: set[str] = set()
        used_slugs = {f.dir_slug for f in self.repo.list_feeds()}
        timeout = self.cfg.feeds.request_timeout_s

        for line in parsed:
            try:
                channel_id, feed_url = self._resolve_source(line, timeout)
            except Exception as exc:  # noqa: BLE001
                log.error("Feed-Zeile nicht auflösbar (%s): %s", line.raw.strip(), exc)
                report.feeds_failed += 1
                continue

            feed = self.repo.get_feed_by_channel_id(channel_id)
            fetch = fetch_feed(
                feed_url, timeout_s=timeout,
                etag=feed.etag if feed else None,
                last_modified=feed.last_modified if feed else None,
                conditional=self.cfg.feeds.use_conditional_get and feed is not None,
            )

            if feed is None:
                title = fetch.channel_title
                slug = self._unique_slug(line.display_name or title or channel_id, used_slugs)
                used_slugs.add(slug)
                feed = self.repo.create_feed(
                    channel_id=channel_id, feed_url=feed_url, dir_slug=slug,
                    channel_title=title, display_name=line.display_name,
                    resolved_from=line.handle,
                )
                log.info("Neuer Feed: %s -> %s", feed.name, slug)
            elif not feed.is_active:
                self.repo.reactivate_feed(feed.id, display_name=line.display_name)

            keep.add(channel_id)
            report.feeds_checked += 1

            if fetch.error:
                log.warning("Feed-Abruf %s: %s", feed.name, fetch.error)
                self.repo.set_feed_error(feed.id, fetch.error)
                report.feeds_failed += 1
                continue
            if fetch.not_modified:
                log.debug("Feed %s unverändert (304)", feed.name)
                self.repo.mark_feed_checked(
                    feed.id, etag=fetch.etag, last_modified=fetch.last_modified
                )
                continue

            if fetch.channel_title and fetch.channel_title != feed.channel_title:
                self.repo.update_channel_title(feed.id, fetch.channel_title)
                feed.channel_title = fetch.channel_title

            is_first_run = feed.last_checked_at is None
            new_count = self._ingest_entries(feed, fetch.entries, opts, is_first_run)
            report.new_videos += new_count

            self.repo.mark_feed_checked(
                feed.id, etag=fetch.etag, last_modified=fetch.last_modified
            )
            time.sleep(self.cfg.feeds.delay_between_s)

        deactivated = self.repo.deactivate_missing_feeds(keep)
        if deactivated:
            log.info("%d Feed(s) nicht mehr in feeds.txt -> is_active = 0", deactivated)

    def _resolve_source(self, line, timeout: float) -> tuple[str, str]:
        if line.channel_id and line.feed_url:
            return line.channel_id, line.feed_url
        if line.channel_id:
            return line.channel_id, feed_url_for_channel(line.channel_id)
        if line.feed_url and line.channel_id is None:
            # videos.xml-URL ohne extrahierbare Kanal-ID: Kanal-ID aus dem Feed lesen
            probe = fetch_feed(line.feed_url, timeout_s=timeout, conditional=False)
            if probe.channel_id:
                return probe.channel_id, line.feed_url
            raise ValueError("Kanal-ID nicht aus Feed lesbar")
        # handle: Auflösungs-Cache prüfen (spec 4.1: genau einmal)
        cached = self.repo.get_feed_by_resolved_from(line.handle)
        if cached:
            return cached.channel_id, cached.feed_url
        channel_id = resolve_handle(line.handle, timeout_s=timeout)
        return channel_id, feed_url_for_channel(channel_id)

    @staticmethod
    def _unique_slug(source: str, used: set[str]) -> str:
        base = slugify(source) or "kanal"
        slug = base
        n = 2
        while slug in used:
            slug = f"{base}_{n}"
            n += 1
        return slug

    # --------------------------------------------------------------- ingest
    def _ingest_entries(self, feed: Feed, entries: list[FeedEntry],
                        opts: RunOptions, is_first_run: bool) -> int:
        entries = sorted(entries, key=lambda e: e.published_at, reverse=True)
        fresh = [e for e in entries if not self.repo.video_exists(e.video_id)]
        if not fresh:
            return 0

        if not is_first_run:
            for entry in fresh:
                self.repo.add_video(entry, feed.id, status="discovered")
            return len(fresh)

        mode = opts.initial_mode
        if mode == "mark-seen":
            for entry in fresh:
                self.repo.add_video(entry, feed.id, status="skipped",
                                    skip_reason="initial_sync")
            return len(fresh)

        if mode == "backfill":
            for entry in fresh[:_BACKFILL_MAX]:
                self.repo.add_video(entry, feed.id, status="discovered")
            for entry in fresh[_BACKFILL_MAX:]:
                self.repo.add_video(entry, feed.id, status="skipped",
                                    skip_reason="initial_sync")
            return len(fresh)

        return self._ingest_latest(feed, fresh, opts)

    def _ingest_latest(self, feed: Feed, fresh: list[FeedEntry],
                       opts: RunOptions) -> int:
        """Neu -> alt durchgehen, je Kandidat Metadaten holen, den ersten mit
        ausreichender Dauer als ``discovered`` anlegen (spec 6.2)."""
        min_seconds = self._min_duration_seconds(opts)
        chosen: str | None = None
        probed = 0

        for entry in fresh:
            if chosen is not None or probed >= _LATEST_PROBE_MAX:
                self.repo.add_video(entry, feed.id, status="skipped",
                                    skip_reason="initial_sync")
                continue
            probed += 1
            meta = captions.probe(entry.url)
            if meta.error:
                log.warning("latest-Probe %s: %s", entry.video_id, meta.error)
                self.repo.add_video(entry, feed.id, status="skipped",
                                    skip_reason="initial_sync")
                continue
            if meta.duration_s is not None and meta.duration_s < min_seconds:
                self.repo.add_video(entry, feed.id, status="skipped",
                                    skip_reason="too_short",
                                    duration_s=meta.duration_s)
                continue
            self.repo.add_video(entry, feed.id, status="discovered",
                                duration_s=meta.duration_s)
            chosen = entry.video_id

        if chosen is None:
            log.info("Feed %s: anfangs kein geeignetes Video gefunden", feed.name)
        return len(fresh)

    # -------------------------------------------------------------- process
    def process(self, opts: RunOptions, report: RunReport) -> None:
        feed_ids = self._feed_ids(opts.feed_channel_ids)
        since = self._normalise_since(opts.since)
        videos = self.repo.claim_videos(
            max_attempts=self.cfg.transcripts.max_attempts,
            limit=opts.limit, feed_ids=feed_ids, since=since,
            asr_only=opts.asr_only,
        )
        if not videos:
            log.info("Keine Videos zu verarbeiten")
            return

        log.info("%d Video(s) zu verarbeiten", len(videos))
        for video in videos:
            feed = self._feed_for(video.feed_id)
            try:
                self._process_one(video, feed, opts, report)
            except Exception as exc:  # noqa: BLE001 - ein Video darf den Lauf nie beenden
                log.exception("Unerwarteter Fehler bei %s", video.video_id)
                self._fail(video, f"unknown: {exc}", report)

    def _process_one(self, video: Video, feed: Feed, opts: RunOptions,
                     report: RunReport) -> None:
        if opts.dry_run:
            log.info("[dry-run] würde verarbeiten: %s (%s)", video.title, video.video_id)
            return

        self.repo.mark_processing(video.video_id)
        current = self.repo.get_video(video.video_id) or video

        meta = captions.probe(video.url)
        if meta.error:
            self._fail(current, meta.error, report)
            return

        if meta.duration_s is not None:
            self.repo.set_duration(video.video_id, meta.duration_s)
            current.duration_s = meta.duration_s

        min_seconds = self._min_duration_seconds(opts)
        if not opts.asr_only and meta.duration_s is not None and meta.duration_s < min_seconds:
            self.repo.skip_video(video.video_id, "too_short", duration_s=meta.duration_s)
            report.skipped += 1
            log.info("Übersprungen (zu kurz, %ds): %s", meta.duration_s, video.title)
            return

        result = None
        if not opts.asr_only:
            result = captions.fetch_captions(
                meta, self.cfg.transcripts.languages,
                prefer_manual=self.cfg.transcripts.prefer_manual,
                prefer_original=self.cfg.transcripts.prefer_original,
                paragraph_pause_s=self.cfg.transcripts.paragraph_pause_s,
                chapter_headings=self.cfg.transcripts.chapter_headings,
            )

        if result is None:
            result = self._try_asr(current, meta, opts)

        if result is None:
            self.repo.set_no_transcript(video.video_id)
            report.no_transcript += 1
            log.info("Kein Transkript: %s", video.title)
            return

        tpath, mpath = write_transcript(self.cfg, feed, current, result)
        self.repo.finish_done(video.video_id, result,
                              transcript_path=tpath, metadata_path=mpath)
        self._upload_to_drive(feed, tpath, mpath)
        report.processed += 1
        if result.source == "asr":
            report.via_asr += 1
        else:
            report.via_captions += 1
        log.info("Fertig (%s): %s", result.source, video.title)

    def _try_asr(self, video: Video, meta, opts: RunOptions):
        if opts.no_asr or not self.cfg.asr.enabled:
            return None
        max_seconds = self.cfg.asr.max_duration_min * 60
        if meta.duration_s is not None and meta.duration_s > max_seconds:
            log.info("ASR übersprungen (zu lang, %ds): %s", meta.duration_s, video.title)
            return None
        try:
            return asr.transcribe(
                video.url, cfg=self.cfg.asr,
                temp_dir=self.cfg.paths.temp_dir or None,
            )
        except asr.AsrUnavailable as exc:
            log.warning("ASR nicht verfügbar: %s", exc)
            return None

    def _upload_to_drive(self, feed: Feed, tpath: str, mpath: str) -> None:
        """Lädt Transkript (+ Sidecar) nach Google Drive hoch, falls konfiguriert.

        Ein Upload-Fehler lässt das Video als 'done' stehen - die lokale Datei
        ist bereits geschrieben und maßgeblich, es wird nur eine Warnung
        geloggt statt den Lauf abzubrechen."""
        if not self.cfg.gdrive.enabled or self._drive_failed:
            return
        if self._drive is None:
            try:
                self._drive = DriveUploader(self.cfg.gdrive)
            except GDriveUnavailable as exc:
                log.warning("Google-Drive-Upload deaktiviert: %s", exc)
                self._drive_failed = True
                return

        output_dir = self.cfg.paths.output_dir
        paths = [output_dir / tpath]
        if self.cfg.gdrive.upload_sidecar:
            paths.append(output_dir / mpath)
        try:
            self._drive.upload(feed.dir_slug, *paths)
        except GDriveUnavailable as exc:
            log.warning("Drive-Upload fehlgeschlagen für %s: %s", tpath, exc)

    # --------------------------------------------------------------- helpers
    def _fail(self, video: Video, cause: str, report: RunReport) -> None:
        attempts = video.attempts
        backoff = self.cfg.transcripts.retry_backoff_s
        idx = min(max(attempts - 1, 0), len(backoff) - 1)
        next_retry = None
        if attempts < self.cfg.transcripts.max_attempts:
            next_retry = iso_utc(_now() + timedelta(seconds=backoff[idx]))
        self.repo.fail_video(video.video_id, cause, next_retry_at=next_retry)
        report.failed += 1
        log.warning("Fehlgeschlagen (%s, Versuch %d): %s", cause, attempts, video.title)

    def _min_duration_seconds(self, opts: RunOptions) -> int:
        minutes = opts.min_duration_min
        if minutes is None:
            minutes = self.cfg.filters.min_duration_min
        return minutes * 60

    def _feed_ids(self, channel_ids: list[str]) -> list[int] | None:
        if not channel_ids:
            return None
        ids: list[int] = []
        for cid in channel_ids:
            feed = self.repo.get_feed_by_channel_id(cid)
            if feed:
                ids.append(feed.id)
            else:
                log.warning("Unbekannter Feed: %s", cid)
        return ids or [-1]

    def _feed_for(self, feed_id: int) -> Feed:
        for feed in self.repo.list_feeds():
            if feed.id == feed_id:
                return feed
        raise KeyError(feed_id)

    @staticmethod
    def _normalise_since(since: str | None) -> str | None:
        if not since:
            return None
        dt = datetime.fromisoformat(since).replace(tzinfo=UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
