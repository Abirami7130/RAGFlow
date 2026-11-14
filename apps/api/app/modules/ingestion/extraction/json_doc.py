"""JSON extraction using the stdlib json module."""

from __future__ import annotations

import json
import re
from typing import Any

from app.domain.extraction import ExtractionError, RawBlock
from app.modules.ingestion.extraction.decode import decode_textual

MAX_NESTING_DEPTH = 64
MAX_BLOCKS = 5_000
_SCALAR_ARRAY_CHUNK = 50
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class JSONObject(dict):
    """Ordered JSON object with duplicate-key tracking from object_pairs_hook."""

    __slots__ = ("_duplicate_keys",)

    def __init__(self) -> None:
        super().__init__()
        self._duplicate_keys: frozenset[str] = frozenset()


def _json_unreadable(message: str) -> ExtractionError:
    return ExtractionError("json_unreadable", message)


def _object_pairs(pairs: list[tuple[str, Any]]) -> JSONObject:
    obj = JSONObject()
    duplicates: set[str] = set()
    for key, value in pairs:
        if key in obj:
            duplicates.add(key)
        obj[key] = value
    obj._duplicate_keys = frozenset(duplicates)
    return obj


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _scalar_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return str(value)


def _key_segment(key: str) -> str:
    if _KEY_RE.fullmatch(key):
        return f".{key}"
    return f"[{json.dumps(key, ensure_ascii=False)}]"


def _locator(json_path: str) -> dict[str, object]:
    return {
        "source_type": "json",
        "json_path": json_path,
        "heading_path": [],
    }


def _append_block(
    blocks: list[RawBlock],
    counter: list[int],
    *,
    block_type: str,
    text: str,
    json_path: str,
    metadata: dict[str, object] | None = None,
) -> None:
    counter[0] += 1
    if counter[0] > MAX_BLOCKS:
        raise _json_unreadable(f"The JSON document exceeds the {MAX_BLOCKS}-block limit.")
    blocks.append(
        RawBlock(
            block_type=block_type,
            text=text,
            locator=_locator(json_path),
            metadata=dict(metadata or {}),
        )
    )


def _emit_object_block(
    scalars: list[tuple[str, Any]],
    json_path: str,
    duplicate_keys: frozenset[str],
    blocks: list[RawBlock],
    counter: list[int],
) -> None:
    if not scalars:
        return
    lines = [f"{key}: {_scalar_text(scalar)}" for key, scalar in scalars]
    metadata: dict[str, object] = {}
    if duplicate_keys & {key for key, _ in scalars}:
        metadata["duplicate_keys"] = True
    _append_block(
        blocks,
        counter,
        block_type="table",
        text="\n".join(lines),
        json_path=json_path,
        metadata=metadata,
    )


def _emit_scalar_array(
    items: list[Any],
    json_path: str,
    blocks: list[RawBlock],
    counter: list[int],
) -> None:
    if len(items) <= _SCALAR_ARRAY_CHUNK:
        text = "\n".join(f"[{index}]: {_scalar_text(value)}" for index, value in enumerate(items))
        _append_block(
            blocks,
            counter,
            block_type="table",
            text=text,
            json_path=json_path,
        )
        return

    for start in range(0, len(items), _SCALAR_ARRAY_CHUNK):
        chunk = items[start : start + _SCALAR_ARRAY_CHUNK]
        end = start + len(chunk) - 1
        chunk_path = f"{json_path}[{start}:{end}]"
        text = "\n".join(f"[{start + offset}]: {_scalar_text(value)}" for offset, value in enumerate(chunk))
        _append_block(
            blocks,
            counter,
            block_type="table",
            text=text,
            json_path=chunk_path,
        )


def _walk(value: Any, json_path: str, depth: int, blocks: list[RawBlock], counter: list[int]) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise _json_unreadable(f"The JSON document exceeds the {MAX_NESTING_DEPTH}-level nesting limit.")

    if isinstance(value, JSONObject):
        scalars: list[tuple[str, Any]] = []
        for key, child in value.items():
            if _is_scalar(child):
                scalars.append((key, child))
            else:
                _walk(child, f"{json_path}{_key_segment(key)}", depth + 1, blocks, counter)
        _emit_object_block(scalars, json_path, value._duplicate_keys, blocks, counter)
        return

    if isinstance(value, list):
        if value and all(_is_scalar(item) for item in value):
            _emit_scalar_array(value, json_path, blocks, counter)
            return
        for index, child in enumerate(value):
            _walk(child, f"{json_path}[{index}]", depth + 1, blocks, counter)
        return

    if _is_scalar(value):
        _append_block(
            blocks,
            counter,
            block_type="paragraph",
            text=_scalar_text(value),
            json_path=json_path,
        )


def extract_json(data: bytes) -> list[RawBlock]:
    text = decode_textual(data)
    try:
        root = json.loads(text, object_pairs_hook=_object_pairs)
    except json.JSONDecodeError as exc:
        raise _json_unreadable("The JSON could not be parsed.") from exc

    blocks: list[RawBlock] = []
    counter = [0]
    _walk(root, "$", 0, blocks, counter)
    return blocks