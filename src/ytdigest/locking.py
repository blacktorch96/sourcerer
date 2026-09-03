"""Single-Instance-Lock über O_CREAT | O_EXCL. Stale, wenn älter als 12 h."""

from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

STALE_AFTER_S = 12 * 3600


class LockHeld(RuntimeError):
    """Ein anderer Lauf hält bereits das Lock."""


def _read_lock(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _is_stale(payload: dict | None) -> bool:
    if not payload:
        return True
    started = payload.get("started_at", 0)
    return (time.time() - float(started)) > STALE_AFTER_S


@contextlib.contextmanager
def single_instance(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "started_iso": datetime.now(UTC).isoformat(),
    }
    encoded = json.dumps(payload).encode("utf-8")

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if _is_stale(_read_lock(lock_path)):
            with contextlib.suppress(OSError):
                lock_path.unlink()
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            raise LockHeld(f"Lock gehalten: {lock_path}") from None

    try:
        os.write(fd, encoded)
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()
