"""In-memory extraction types. No database or network access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RawBlock:
    block_type: str
    text: str
    locator: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(slots=True)
class NormalizedBlock:
    sequence: int
    block_type: str
    text: str
    locator: dict[str, Any]
    metadata: dict[str, Any]


class ExtractionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
