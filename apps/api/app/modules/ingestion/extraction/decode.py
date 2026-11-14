"""Deterministic text decoding. No charset heuristics."""

from __future__ import annotations

from app.domain.extraction import ExtractionError

_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_LE_BOM = b"\xff\xfe"
_UTF16_BE_BOM = b"\xfe\xff"


def decode_textual(data: bytes) -> str:
    """Accept UTF-8 (optional BOM) or UTF-16 with a BOM. Otherwise require strict UTF-8."""
    try:
        if data.startswith(_UTF16_LE_BOM) or data.startswith(_UTF16_BE_BOM):
            text = data.decode("utf-16")
        elif data.startswith(_UTF8_BOM):
            text = data.decode("utf-8-sig")
        else:
            text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ExtractionError(
            "invalid_encoding",
            "The file is not valid UTF-8 or a supported BOM encoding.",
        ) from exc
    if "\x00" in text:
        raise ExtractionError("nul_in_text", "The file contains a NUL character.")
    return text.replace("\r\n", "\n").replace("\r", "\n")
