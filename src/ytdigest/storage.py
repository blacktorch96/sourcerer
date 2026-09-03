"""Atomares Schreiben von Transkript und JSON-Sidecar (spec 7)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from ytdigest import __version__
from ytdigest.config import Config
from ytdigest.models import Feed, TranscriptResult, Video
from ytdigest.naming import build_basename

SCHEMA_VERSION = 1


def _parse_dt(value: str) -> datetime:
    cleaned = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _unique_basename(target_dir: Path, basename: str, video_id: str,
                     *, force_id: bool) -> str:
    if force_id:
        return f"{basename}_{video_id}"
    if not (target_dir / f"{basename}.txt").exists():
        return basename
    return f"{basename}_{video_id}"


def write_transcript(cfg: Config, feed: Feed, video: Video,
                     result: TranscriptResult) -> tuple[str, str]:
    """Schreibt beide Dateien und gibt (transcript_path, metadata_path)
    relativ zu ``cfg.paths.output_dir`` zurück."""
    published = _parse_dt(video.published_at)
    target_dir = cfg.paths.output_dir / feed.dir_slug
    target_dir.mkdir(parents=True, exist_ok=True)

    base = build_basename(
        published, video.title,
        max_len=cfg.output.max_filename_len,
        video_id=video.video_id,
        force_id=False,
    )
    base = _unique_basename(target_dir, base, video.video_id,
                            force_id=cfg.output.include_video_id)

    txt_path = target_dir / f"{base}.txt"
    json_path = target_dir / f"{base}.json"

    sidecar = {
        "schema_version": SCHEMA_VERSION,
        "video_id": video.video_id,
        "url": video.url,
        "title": video.title,
        "channel": {
            "id": feed.channel_id,
            "title": feed.channel_title,
            "dir_slug": feed.dir_slug,
        },
        "published_at": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": video.duration_s,
        "transcript": {
            "source": result.source,
            "language": result.language,
            "asr_model": result.asr_model,
            "char_count": result.char_count,
            "word_count": result.word_count,
            "file": txt_path.name,
        },
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_version": __version__,
    }

    _atomic_write(txt_path, result.text.strip() + "\n")
    _atomic_write(json_path, json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n")

    output_dir = cfg.paths.output_dir
    return (
        str(txt_path.relative_to(output_dir)),
        str(json_path.relative_to(output_dir)),
    )
