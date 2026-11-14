"""Unit and worker tests for DOCX extraction."""

from __future__ import annotations

import pytest

pytest_plugins = ["test_canonical_blocks"]

from app.domain.extraction import ExtractionError
from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks
from app.modules.ingestion.extraction.docx import MAX_ZIP_ENTRIES, extract_docx
from tests.docx_fixtures import (
    build_docx_with_header,
    build_docx_with_list_and_table,
    build_heading_docx,
    build_zip_entry_overflow_docx,
)

from tests.test_canonical_blocks import (  # noqa: PLC0415 — shared harness
        INDEX_STAGE_TAIL,
    _block_count,
    _enqueue,
    _events,
    _execute,
)


def _normalized_docx(data: bytes):
    return normalize_blocks(extract_raw_blocks("docx", data))


def test_docx_heading_ancestry() -> None:
    blocks = _normalized_docx(build_heading_docx())
    assert [(block.block_type, block.text, block.locator["heading_path"]) for block in blocks] == [
        ("heading", "H1", ["H1"]),
        ("paragraph", "Body under H1", ["H1"]),
        ("heading", "H2", ["H1", "H2"]),
        ("paragraph", "Body under H2", ["H1", "H2"]),
    ]
    for block in blocks:
        assert block.locator == {
            "source_type": "docx",
            "heading_path": block.locator["heading_path"],
        }
        assert "page" not in block.locator


def test_docx_omits_header_text() -> None:
    blocks = _normalized_docx(build_docx_with_header())
    assert [block.text for block in blocks] == ["Only body text"]
    assert all("Header" not in block.text for block in blocks)


def test_docx_list_and_table_blocks() -> None:
    blocks = _normalized_docx(build_docx_with_list_and_table())
    assert blocks[0].block_type == "heading"
    assert blocks[1].block_type == "list_item"
    assert blocks[2].block_type == "list_item"
    assert [block.block_type for block in blocks[3:]] == ["table", "table"]
    assert blocks[3].metadata == {"table_index": 0, "row_index": 0}
    assert blocks[3].text == "A | 1"


def test_docx_zip_entry_limit_is_permanent() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_docx(build_zip_entry_overflow_docx())
    assert exc.value.code == "docx_unreadable"
    assert str(MAX_ZIP_ENTRIES) in exc.value.message


def test_docx_malformed_is_unreadable() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_docx(b"PK\x03\x04not-a-valid-docx")
    assert exc.value.code == "docx_unreadable"


def test_worker_ingests_docx_into_canonical_blocks(kb_ctx: dict) -> None:
    prepared = _enqueue(
        kb_ctx,
        build_heading_docx(),
        source_type="docx",
        filename="notes.docx",
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