"""Typer-App. Kommandos delegieren nur an pipeline / repo / storage."""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ytdigest import __version__
from ytdigest.config import Config, load_config
from ytdigest.db.repo import Repo
from ytdigest.db.schema import connect, init_db
from ytdigest.feeds.parser import parse_line
from ytdigest.locking import LockHeld, single_instance
from ytdigest.logsetup import setup_logging
from ytdigest.pipeline import INITIAL_MODES, Pipeline, RunOptions

app = typer.Typer(add_completion=False,
                  help="YouTube- und Podcast-Feed-Überwachung und Transkriptbeschaffung.")
feeds_app = typer.Typer(help="Feed-Liste verwalten.")
app.add_typer(feeds_app, name="feeds")
local_app = typer.Typer(help="Lokale Videodateien scannen und transkribieren.")
app.add_typer(local_app, name="local")
console = Console()

EXIT_OK = 0
EXIT_HAD_FAILURES = 1
EXIT_CONFIG = 2
EXIT_LOCKED = 3
EXIT_INTERRUPTED = 130


class AppState:
    cfg: Config
    config_path: Path | None = None
    verbose: bool = False
    quiet: bool = False


state = AppState()


@app.callback()
def main(
    config: Path | None = typer.Option(None, "--config", help="Pfad zu config.toml"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8")  # spec 9: Konsole UTF-8 erzwingen

    state.config_path = config
    state.verbose = verbose
    state.quiet = quiet
    try:
        state.cfg = load_config(config)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Konfigurationsfehler:[/red] {exc}")
        raise typer.Exit(EXIT_CONFIG) from exc
    setup_logging(state.cfg.paths.log_file, verbose=verbose, quiet=quiet)


def _open_db(*, create: bool = False) -> sqlite3.Connection:
    db_path = state.cfg.paths.database
    if not db_path.exists() and not create:
        console.print(f"[red]Datenbank fehlt:[/red] {db_path} — zuerst 'ytdigest init'")
        raise typer.Exit(EXIT_CONFIG)
    return init_db(db_path) if create else connect(db_path)


def _lock_path() -> Path:
    return state.cfg.paths.database.with_suffix(".lock")


# --------------------------------------------------------------------- init
@app.command()
def init() -> None:
    """Datenbank und Verzeichnisse anlegen."""
    cfg = state.cfg
    for directory in (cfg.paths.database.parent, cfg.paths.output_dir,
                      cfg.paths.log_file.parent):
        directory.mkdir(parents=True, exist_ok=True)
    conn = _open_db(create=True)
    conn.close()
    if not cfg.paths.feeds_file.exists():
        cfg.paths.feeds_file.write_text("# Eine Quelle pro Zeile\n", encoding="utf-8")
    console.print(f"[green]Bereit.[/green] Datenbank: {cfg.paths.database}")


# ---------------------------------------------------------------------- run
@app.command()
def run(
    initial_mode: str = typer.Option("latest", "--initial-mode",
                                     help=f"Eins von {', '.join(INITIAL_MODES)}"),
    min_duration: int | None = typer.Option(None, "--min-duration",
                                               help="Dauerfilter (Minuten) überschreiben"),
    limit: int | None = typer.Option(None, "--limit", help="max. Videos pro Lauf"),
    feed: list[str] = typer.Option([], "--feed", help="auf Kanal-IDs einschränken"),
    since: str | None = typer.Option(None, "--since", help="nur Videos ab YYYY-MM-DD"),
    no_asr: bool = typer.Option(False, "--no-asr", help="ASR-Fallback deaktivieren"),
    asr_only: bool = typer.Option(False, "--asr-only",
                                  help="nur no_transcript-Videos per ASR nachholen"),
    sync_only: bool = typer.Option(False, "--sync-only", help="nur Feeds abrufen"),
    dry_run: bool = typer.Option(False, "--dry-run", help="nichts schreiben"),
) -> None:
    """Sync und Verarbeitung."""
    if initial_mode not in INITIAL_MODES:
        console.print(f"[red]Ungültiger --initial-mode:[/red] {initial_mode}")
        raise typer.Exit(EXIT_CONFIG)

    cfg = state.cfg
    effective_no_asr = no_asr
    if cfg.asr.enabled and not no_asr and not sync_only and shutil.which("ffmpeg") is None:
        console.print("[yellow]ffmpeg nicht gefunden — ASR-Pfad wird für diesen "
                      "Lauf übersprungen.[/yellow]")
        effective_no_asr = True

    opts = RunOptions(
        initial_mode=initial_mode, min_duration_min=min_duration, limit=limit,
        feed_channel_ids=list(feed), since=since, no_asr=effective_no_asr,
        asr_only=asr_only, sync_only=sync_only, dry_run=dry_run,
    )

    try:
        with single_instance(_lock_path()):
            conn = _open_db()
            try:
                report = Pipeline(cfg, conn).run(opts)
            finally:
                conn.close()
    except LockHeld:
        console.print("[red]Ein anderer Lauf hält bereits das Lock.[/red]")
        raise typer.Exit(EXIT_LOCKED) from None
    except KeyboardInterrupt:
        console.print("[yellow]Abgebrochen.[/yellow]")
        raise typer.Exit(EXIT_INTERRUPTED) from None

    _print_report(report)
    raise typer.Exit(report.exit_code)


def _print_report(report) -> None:
    console.print()
    console.print(
        f"Feeds geprüft: {report.feeds_checked} (Fehler {report.feeds_failed})  "
        f"neue Videos: {report.new_videos}"
    )
    console.print(
        f"verarbeitet: {report.processed}  "
        f"Captions: {report.via_captions}  ASR: {report.via_asr}  "
        f"ohne Transkript: {report.no_transcript}  "
        f"übersprungen: {report.skipped}  fehlgeschlagen: {report.failed}"
    )
    console.print(f"Laufzeit: {report.runtime_s:.1f}s")


# -------------------------------------------------------------------- status
@app.command()
def status() -> None:
    """Zählwerte je Zustand, letzte Fehler."""
    conn = _open_db()
    repo = Repo(conn)
    counts = repo.counts_by_status()
    table = Table("Zustand", "Anzahl")
    for name in ("discovered", "processing", "done", "no_transcript", "failed",
                 "skipped"):
        table.add_row(name, str(counts.get(name, 0)))
    console.print(table)

    errors = repo.recent_errors(10)
    if errors:
        console.print("\n[bold]Letzte Fehler[/bold]")
        for video in errors:
            console.print(f"  {video.video_id}  {video.last_error}  — {video.title}")
    conn.close()


# --------------------------------------------------------------------- retry
@app.command()
def retry(
    no_transcript: bool = typer.Option(False, "--no-transcript",
                                       help="auch no_transcript-Videos neu einplanen"),
    skipped: bool = typer.Option(False, "--skipped",
                                 help="auch anfangs übersprungene Videos (initial_sync, "
                                      "z. B. ältere Podcast-Folgen) neu einplanen"),
    feed: list[str] = typer.Option([], "--feed", help="auf Kanal-IDs einschränken"),
) -> None:
    """Fehlgeschlagene bzw. übersprungene Videos neu einplanen."""
    conn = _open_db()
    repo = Repo(conn)
    feed_ids = None
    if feed:
        feed_ids = [f.id for cid in feed if (f := repo.get_feed_by_channel_id(cid))]
    n = repo.requeue_failed(include_no_transcript=no_transcript, feed_ids=feed_ids)
    if skipped:
        n += repo.requeue_skipped(feed_ids=feed_ids)
    conn.close()
    console.print(f"[green]{n}[/green] Video(s) auf 'discovered' zurückgesetzt.")


# --------------------------------------------------------------------- paths
@app.command()
def paths() -> None:
    """Aufgelöste Pfade ausgeben (Debugging)."""
    cfg = state.cfg
    table = Table("Schlüssel", "Wert")
    table.add_row("config", str(cfg.source_path or "(Defaults)"))
    table.add_row("feeds_file", str(cfg.paths.feeds_file.resolve()))
    table.add_row("database", str(cfg.paths.database.resolve()))
    table.add_row("output_dir", str(cfg.paths.output_dir.resolve()))
    table.add_row("log_file", str(cfg.paths.log_file.resolve()))
    table.add_row("temp_dir", cfg.paths.temp_dir or "(Systemtemp)")
    table.add_row("version", __version__)
    console.print(table)


# ---------------------------------------------------------------- feeds sync
@feeds_app.command("sync")
def feeds_sync() -> None:
    """feeds.txt in die DB übernehmen (ohne Verarbeitung)."""
    conn = _open_db()
    try:
        report = Pipeline(state.cfg, conn).run(RunOptions(sync_only=True))
    finally:
        conn.close()
    console.print(f"Feeds geprüft: {report.feeds_checked}, neue Videos: {report.new_videos}")


@feeds_app.command("list")
def feeds_list() -> None:
    """Feeds mit Videoanzahl und letztem Abruf."""
    conn = _open_db()
    repo = Repo(conn)
    per_feed = repo.video_counts_per_feed()
    table = Table("Slug", "Kanal", "Kanal-ID", "Videos", "Letzter Abruf", "Aktiv")
    for feed in repo.list_feeds():
        table.add_row(
            feed.dir_slug, feed.name, feed.channel_id,
            str(per_feed.get(feed.id, 0)),
            feed.last_checked_at or "—",
            "ja" if feed.is_active else "nein",
        )
    console.print(table)
    conn.close()


@feeds_app.command("add")
def feeds_add(source: str = typer.Argument(
                  ..., help="URL, Kanal-ID, @handle oder 'podcast:<RSS-URL>'"),
              name: str | None = typer.Option(None, "--name", help="Anzeigename")) -> None:
    """Zeile an feeds.txt anhängen und syncen."""
    line = f"{source} | {name}" if name else source
    try:
        parse_line(line)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_CONFIG) from exc

    feeds_file = state.cfg.paths.feeds_file
    existing = feeds_file.read_text(encoding="utf-8") if feeds_file.exists() else ""
    if line not in existing.splitlines():
        with feeds_file.open("a", encoding="utf-8", newline="\n") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(line + "\n")
        console.print(f"[green]Hinzugefügt:[/green] {line}")

    conn = _open_db()
    try:
        Pipeline(state.cfg, conn).run(RunOptions(sync_only=True))
    finally:
        conn.close()


# ---------------------------------------------------------------- local scan
@local_app.command("scan")
def local_scan(
    directory: Path = typer.Argument(..., help="Wurzelverzeichnis; Unterordner = Kanäle"),
    limit: int | None = typer.Option(None, "--limit", help="max. Videos pro Lauf"),
    delete_after_success: bool = typer.Option(
        False, "--delete-after-success",
        help="Quelldatei nach erfolgreicher Transkription löschen"),
    no_asr: bool = typer.Option(False, "--no-asr", help="ASR deaktivieren (nur scannen/einplanen)"),
    sync_only: bool = typer.Option(False, "--sync-only", help="nur scannen, nicht transkribieren"),
    dry_run: bool = typer.Option(False, "--dry-run", help="nichts schreiben/löschen"),
) -> None:
    """Verzeichnis nach lokalen Videos durchsuchen und per ASR transkribieren.

    Erwartet: <directory>/<kanal>/<datei>.<ext> - je Unterordner ein Kanal,
    Videodateien direkt darin. Details siehe README.
    """
    if not directory.is_dir():
        console.print(f"[red]Kein Verzeichnis:[/red] {directory}")
        raise typer.Exit(EXIT_CONFIG)

    cfg = state.cfg
    effective_no_asr = no_asr
    if cfg.asr.enabled and not no_asr and not sync_only and shutil.which("ffmpeg") is None:
        console.print("[yellow]ffmpeg nicht gefunden — ASR-Pfad wird für diesen "
                      "Lauf übersprungen.[/yellow]")
        effective_no_asr = True
    if not sync_only and shutil.which("ffprobe") is None:
        console.print("[red]ffprobe nicht gefunden — für lokale Videos zwingend nötig "
                      "(kommt mit ffmpeg mit).[/red]")
        raise typer.Exit(EXIT_CONFIG)

    opts = RunOptions(
        limit=limit, no_asr=effective_no_asr, sync_only=sync_only, dry_run=dry_run,
        delete_source_on_success=delete_after_success,
    )

    try:
        with single_instance(_lock_path()):
            conn = _open_db()
            try:
                report = Pipeline(cfg, conn).run_local(directory, opts)
            finally:
                conn.close()
    except LockHeld:
        console.print("[red]Ein anderer Lauf hält bereits das Lock.[/red]")
        raise typer.Exit(EXIT_LOCKED) from None
    except KeyboardInterrupt:
        console.print("[yellow]Abgebrochen.[/yellow]")
        raise typer.Exit(EXIT_INTERRUPTED) from None

    _print_report(report)
    raise typer.Exit(report.exit_code)


if __name__ == "__main__":  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(EXIT_INTERRUPTED)
