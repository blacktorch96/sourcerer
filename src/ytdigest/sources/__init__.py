"""Transkriptquellen. Liefern ein TranscriptResult oder None, kennen weder DB
noch Dateisystem (spec 12)."""

from ytdigest.sources.vtt import vtt_to_text

__all__ = ["vtt_to_text"]
