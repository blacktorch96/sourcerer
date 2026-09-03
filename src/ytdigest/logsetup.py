"""Logging: RichHandler auf der Konsole, RotatingFileHandler auf paths.log_file."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

_CONFIGURED = False


def setup_logging(log_file: Path, *, verbose: bool = False, quiet: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    console_level = logging.WARNING if quiet else logging.DEBUG if verbose else logging.INFO

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = RichHandler(rich_tracebacks=True, show_path=False, show_time=not quiet)
    console.setLevel(console_level)
    root.addHandler(console)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(file_handler)
    except OSError as exc:  # Konsole reicht, wenn die Datei nicht geht
        logging.getLogger(__name__).warning("Log-Datei nicht schreibbar: %s", exc)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.ERROR)
    _CONFIGURED = True
