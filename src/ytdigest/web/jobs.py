"""Hintergrund-Job-Ausführung für die Web-Oberfläche.

Ein Job (Sync/Run, Backfill, ...) läuft in genau einem Hintergrund-Thread;
``single_instance`` sichert zusätzlich prozessübergreifend gegen einen
parallelen Cron-/CLI-Lauf ab (spec 9: Single-Instance-Lock). Logzeilen werden
während des Laufs live an alle verbundenen Browser-Tabs verteilt (Server-Sent
Events), nicht nur ins Logfile geschrieben.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from ytdigest.config import Config
from ytdigest.db.schema import connect
from ytdigest.locking import LockHeld, lock_path_for, single_instance
from ytdigest.models import RunReport
from ytdigest.pipeline import Pipeline

JobFunc = Callable[[Pipeline], RunReport]

_QUEUE_TIMEOUT_S = 15  # Heartbeat-Intervall, damit SSE-Verbindungen nicht als tot gelten


class _BroadcastHandler(logging.Handler):
    def __init__(self, sinks: list[queue.Queue]) -> None:
        super().__init__()
        self._sinks = sinks

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        for sink in list(self._sinks):
            sink.put({"type": "log", "text": line})


class JobRunner:
    """Genau ein Job zur Zeit; weitere Startversuche schlagen fehl, solange
    einer läuft (siehe ``running``/``start``)."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._sinks: list[queue.Queue] = []
        self.label: str | None = None
        self.last_report: RunReport | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, label: str, func: JobFunc) -> bool:
        """Startet ``func(pipeline)`` im Hintergrund. False, falls schon ein
        Job läuft (Aufrufer zeigt dann einen Hinweis statt eines zweiten Jobs)."""
        with self._lock:
            if self.running:
                return False
            self.label = label
            self.last_report = None
            self.last_error = None
            self._thread = threading.Thread(target=self._run, args=(func,), daemon=True)
            self._thread.start()
            return True

    def _run(self, func: JobFunc) -> None:
        handler = _BroadcastHandler(self._sinks)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                               "%H:%M:%S"))
        logger = logging.getLogger("ytdigest")
        previous_level = logger.level
        if logger.getEffectiveLevel() > logging.INFO:
            # Ohne CLI-Aufruf (der setup_logging() vorschaltet) bleibt der
            # Logger sonst auf dem Python-Default WARNING - dann kämen die
            # normalen Fortschrittsmeldungen (log.info) nie im Live-Log an.
            logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        self._broadcast_log(f"=== {self.label} gestartet ===")
        try:
            with single_instance(lock_path_for(self.cfg.paths.database)):
                conn = connect(self.cfg.paths.database)
                try:
                    self.last_report = func(Pipeline(self.cfg, conn))
                finally:
                    conn.close()
        except LockHeld:
            self.last_error = "Ein anderer Lauf (Cron/CLI) hält bereits das Lock."
            self._broadcast_log(f"FEHLER: {self.last_error}")
        except Exception as exc:  # noqa: BLE001 - ein Job darf den Webserver nie mitreißen
            self.last_error = str(exc)
            logger.exception("Web-Job fehlgeschlagen")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            self._broadcast_log(f"=== {self.label} beendet ===")
            self._broadcast_done()

    def _broadcast_log(self, text: str) -> None:
        for sink in list(self._sinks):
            sink.put({"type": "log", "text": text})

    def _broadcast_done(self) -> None:
        payload: dict[str, Any] = {"type": "done", "error": self.last_error}
        if self.last_report is not None:
            payload["report"] = {
                "feeds_checked": self.last_report.feeds_checked,
                "new_videos": self.last_report.new_videos,
                "processed": self.last_report.processed,
                "via_captions": self.last_report.via_captions,
                "via_asr": self.last_report.via_asr,
                "no_transcript": self.last_report.no_transcript,
                "skipped": self.last_report.skipped,
                "failed": self.last_report.failed,
                "runtime_s": round(self.last_report.runtime_s, 1),
            }
        for sink in list(self._sinks):
            sink.put(payload)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self._sinks.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with contextlib.suppress(ValueError):
            self._sinks.remove(q)

    def stream(self, q: queue.Queue):
        """SSE-Eventstrom für einen einzelnen Abonnenten - Heartbeats halten
        die Verbindung offen, solange ein Job (still) läuft."""
        try:
            while True:
                try:
                    item = q.get(timeout=_QUEUE_TIMEOUT_S)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if item["type"] == "log":
                    yield f"data: {item['text']}\n\n"
                else:
                    yield f"event: done\ndata: {json.dumps(item)}\n\n"
                    break
        finally:
            self.unsubscribe(q)
