"""Turn raw extractor blocks into a dense canonical sequence."""

from __future__ import annotations

import unicodedata

from app.domain.extraction import NormalizedBlock, RawBlock


def _strip_controls(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(char for char in text if char in "\t\n" or unicodedata.category(char) != "Cc")


def _clean_text(text: str) -> str:
    return _strip_controls(text).strip()


def normalize_blocks(blocks: list[RawBlock]) -> list[NormalizedBlock]:
    normalized: list[NormalizedBlock] = []
    for block in blocks:
        text = _clean_text(block.text)
        if not text:
            continue
        locator = {key: value for key, value in block.locator.items() if key not in {"char_start", "char_end"}}
        if "heading_path" in locator:
            path: list[str] = []
            for item in locator["heading_path"]:
                cleaned = _clean_text(str(item))
                if cleaned:
                    path.append(cleaned)
            locator["heading_path"] = path
        normalized.append(
            NormalizedBlock(
                sequence=len(normalized),
                block_type=block.block_type,
                text=text,
                locator=locator,
                metadata=dict(block.metadata),
            )
        )
    return normalized
