"""Textual extraction for the ingestion worker."""

from app.modules.ingestion.extraction.dispatch import extract_raw_blocks
from app.modules.ingestion.extraction.normalize import normalize_blocks

__all__ = ["extract_raw_blocks", "normalize_blocks"]
