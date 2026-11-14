"""Deterministic structure-aware chunk assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.chunking.tokenizer import token_count, token_spans


@dataclass(slots=True)
class BlockSlice:
    block_index: int
    char_start: int | None = None
    char_end: int | None = None


@dataclass(slots=True)
class ChunkDraft:
    sequence: int
    text: str
    token_count: int
    block_start_index: int
    block_end_index: int
    locator_start: dict[str, Any]
    locator_end: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _BlockInput:
    block_type: str
    text: str
    locator: dict[str, Any]


def build_structure_aware_chunks(
    blocks: list[_BlockInput],
    *,
    target_tokens: int,
    overlap_tokens: int,
    split_oversized_blocks: bool,
) -> list[ChunkDraft]:
    if target_tokens < 1:
        raise ValueError("target_tokens must be positive.")
    overlap_tokens = max(0, min(overlap_tokens, target_tokens - 1))

    pending_slices: list[BlockSlice] = []
    chunks: list[ChunkDraft] = []
    sequence = 0

    def block_text(block_index: int, slice_: BlockSlice) -> str:
        block = blocks[block_index]
        start = slice_.char_start or 0
        end = slice_.char_end if slice_.char_end is not None else len(block.text)
        return block.text[start:end]

    def locator_for(block_index: int, slice_: BlockSlice) -> dict[str, Any]:
        locator = dict(blocks[block_index].locator)
        if slice_.char_start is not None:
            locator["char_start"] = slice_.char_start
        if slice_.char_end is not None:
            locator["char_end"] = slice_.char_end
        return locator

    def heading_prefix(first_block_index: int) -> tuple[str, bool]:
        block = blocks[first_block_index]
        if block.block_type == "heading":
            return "", False
        path = block.locator.get("heading_path") or []
        if not path:
            return "", False
        return "\n".join(str(part) for part in path) + "\n\n", True

    def assemble_text(slices: list[BlockSlice]) -> tuple[str, bool]:
        if not slices:
            return "", False
        body = "\n\n".join(block_text(slice_.block_index, slice_) for slice_ in slices)
        prefix, used_prefix = heading_prefix(slices[0].block_index)
        if not prefix:
            return body, used_prefix
        return f"{prefix}{body}", used_prefix

    def sized_tokens(slices: list[BlockSlice]) -> int:
        text, _ = assemble_text(slices)
        return token_count(text)

    def flush(slices: list[BlockSlice]) -> None:
        nonlocal sequence
        if not slices:
            return
        text, heading_prefix_used = assemble_text(slices)
        metadata: dict[str, Any] = {"strategy": "structure_aware"}
        if heading_prefix_used:
            metadata["heading_prefix"] = True
        if len(slices) == 1 and slices[0].char_start is not None:
            metadata["block_split"] = True
        chunks.append(
            ChunkDraft(
                sequence=sequence,
                text=text,
                token_count=token_count(text),
                block_start_index=slices[0].block_index,
                block_end_index=slices[-1].block_index,
                locator_start=locator_for(slices[0].block_index, slices[0]),
                locator_end=locator_for(slices[-1].block_index, slices[-1]),
                metadata=metadata,
            )
        )
        sequence += 1

    def split_oversized_block(block_index: int) -> list[BlockSlice]:
        block = blocks[block_index]
        spans = token_spans(block.text)
        if len(spans) <= target_tokens:
            return [BlockSlice(block_index=block_index)]
        if not split_oversized_blocks:
            return [BlockSlice(block_index=block_index)]

        slices: list[BlockSlice] = []
        token_cursor = 0
        while token_cursor < len(spans):
            window_end = min(len(spans), token_cursor + target_tokens)
            char_start = spans[token_cursor][0]
            char_end = spans[window_end - 1][1]
            slices.append(BlockSlice(block_index=block_index, char_start=char_start, char_end=char_end))
            if window_end >= len(spans):
                break
            token_cursor = max(token_cursor + 1, window_end - overlap_tokens)
        return slices

    for block_index, block in enumerate(blocks):
        stripped = block.text.strip()
        if not stripped:
            continue

        oversized_slices = split_oversized_block(block_index)
        if len(oversized_slices) > 1 or (oversized_slices[0].char_start is not None):
            if pending_slices:
                flush(pending_slices)
                pending_slices = []
            for piece in oversized_slices:
                flush([piece])
            continue

        if pending_slices and block.block_type == "heading":
            flush(pending_slices)
            pending_slices = []

        candidate = [*pending_slices, BlockSlice(block_index=block_index)]
        if pending_slices and sized_tokens(candidate) > target_tokens:
            flush(pending_slices)
            pending_slices = [BlockSlice(block_index=block_index)]
            continue

        pending_slices = candidate

    if pending_slices:
        flush(pending_slices)

    return chunks