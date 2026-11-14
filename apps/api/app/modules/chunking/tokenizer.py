"""Lexical tokenizer shared by chunk sizing and future BM25 indexing."""

from __future__ import annotations

import re
import unicodedata

_APOSTROPHE_CHARS = frozenset({"'", "\u2019", "`"})
_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)


def prepare_text(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    pieces: list[str] = []
    for char in folded:
        if char in _APOSTROPHE_CHARS:
            pieces.append(" ")
        else:
            pieces.append(char)
    return "".join(pieces)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(prepare_text(text))


def token_count(text: str) -> int:
    return len(tokenize(text))


def _prepared_index_map(text: str) -> tuple[str, list[int]]:
    prepared_chars: list[str] = []
    origin_index: list[int] = []
    for index, char in enumerate(text):
        for part in unicodedata.normalize("NFKC", char).casefold():
            if part in _APOSTROPHE_CHARS:
                prepared_chars.append(" ")
                origin_index.append(index)
            else:
                prepared_chars.append(part)
                origin_index.append(index)
    return "".join(prepared_chars), origin_index


def token_spans(text: str) -> list[tuple[int, int]]:
    """Return character spans in the original block text for each token."""
    prepared, origin = _prepared_index_map(text)
    spans: list[tuple[int, int]] = []
    for match in _TOKEN_RE.finditer(prepared):
        start = origin[match.start()]
        end = origin[match.end() - 1] + 1
        spans.append((start, end))
    return spans