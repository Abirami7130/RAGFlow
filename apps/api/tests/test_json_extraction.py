"""Unit and worker tests for JSON extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest_plugins = ["test_canonical_blocks"]

from app.domain.extraction import ExtractionError
from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks
from app.modules.ingestion.extraction.json_doc import MAX_BLOCKS, MAX_NESTING_DEPTH, extract_json

from tests.test_canonical_blocks import (  # noqa: PLC0415 — shared harness
        INDEX_STAGE_TAIL,
    _block_count,
    _enqueue,
    _events,
    _execute,
)

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"


def _normalized_json(data: bytes):
    return normalize_blocks(extract_raw_blocks("json", data))


def test_json_fixture_paths_and_text() -> None:
    blocks = _normalized_json((FIXTURES / "sample.json").read_bytes())
    by_path = {block.locator["json_path"]: block for block in blocks}
    assert by_path["$"].text == "title: Widget\ncount: 3"
    assert by_path["$.details"].text == "color: blue\nsize: 42"
    assert by_path["$.tags"].text == "[0]: alpha\n[1]: beta"
    assert by_path['$.meta["weird.key"]'].text == "inner: value"


def test_json_fixture_escaped_key_path() -> None:
    blocks = _normalized_json((FIXTURES / "sample.json").read_bytes())
    escaped = next(block for block in blocks if block.locator["json_path"] == '$.meta["weird.key"]')
    assert escaped.text == "inner: value"


def test_json_duplicate_keys_keep_last_value() -> None:
    blocks = _normalized_json(b'{"a": 1, "a": 2}')
    assert len(blocks) == 1
    assert blocks[0].text == "a: 2"
    assert blocks[0].metadata.get("duplicate_keys") is True


def test_json_root_scalar() -> None:
    blocks = _normalized_json(b'"hello"')
    assert [(block.block_type, block.locator["json_path"], block.text) for block in blocks] == [
        ("paragraph", "$", "hello")
    ]


def test_json_large_scalar_array_splits_paths() -> None:
    payload = json.dumps(list(range(55))).encode()
    blocks = _normalized_json(payload)
    assert len(blocks) == 2
    assert blocks[0].locator["json_path"] == "$[0:49]"
    assert blocks[1].locator["json_path"] == "$[50:54]"
    assert "[49]: 49" in blocks[0].text
    assert "[54]: 54" in blocks[1].text


def test_json_malformed_is_unreadable() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_json(b'{"a":')
    assert exc.value.code == "json_unreadable"


def test_json_invalid_encoding_is_permanent() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_raw_blocks("json", b"\x80\x81")
    assert exc.value.code == "invalid_encoding"


def test_json_exceeds_block_limit() -> None:
    payload = json.dumps({"items": [{"v": index} for index in range(MAX_BLOCKS + 1)]}).encode()
    with pytest.raises(ExtractionError) as exc:
        extract_json(payload)
    assert exc.value.code == "json_unreadable"
    assert str(MAX_BLOCKS) in exc.value.message


def test_json_exceeds_nesting_limit() -> None:
    nested = b"{"
    nested += b'"l":' * MAX_NESTING_DEPTH
    nested += b'"v":1' + b"}" * (MAX_NESTING_DEPTH + 1)
    with pytest.raises(ExtractionError) as exc:
        extract_json(nested)
    assert exc.value.code == "json_unreadable"


def test_worker_ingests_json_into_canonical_blocks(kb_ctx: dict) -> None:
    prepared = _enqueue(
        kb_ctx,
        (FIXTURES / "sample.json").read_bytes(),
        source_type="json",
        filename="sample.json",
    )
    _execute(prepared["job_id"])
    assert _block_count(prepared["version_id"]) == 4
    events = _events(prepared["trace_id"])
    assert events[0].stage == "extract" and events[0].status == "succeeded"
    assert events[1].stage == "normalize" and events[1].status == "succeeded"
    assert events[2].stage == "chunk" and events[2].status == "succeeded"
    assert [(event.stage, event.status, event.skip_reason) for event in events[3:]] == list(
        INDEX_STAGE_TAIL
    )