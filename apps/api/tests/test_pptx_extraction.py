"""Unit and worker tests for PPTX extraction."""

from __future__ import annotations

import pytest

pytest_plugins = ["test_canonical_blocks"]

from app.domain.extraction import ExtractionError
from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks
from app.modules.ingestion.extraction.pptx import MAX_ZIP_ENTRIES, extract_pptx
from tests.pptx_fixtures import (
    build_empty_slides_deck,
    build_picture_only_slide,
    build_two_slide_deck,
    build_zip_entry_overflow_pptx,
)

from tests.test_canonical_blocks import (  # noqa: PLC0415 — shared harness
        INDEX_STAGE_TAIL,
    _block_count,
    _enqueue,
    _events,
    _execute,
)


def _normalized_pptx(data: bytes):
    return normalize_blocks(extract_raw_blocks("pptx", data))


def test_pptx_slide_locators_and_title_heading() -> None:
    blocks = _normalized_pptx(build_two_slide_deck())
    slide_two_heading = next(
        block for block in blocks if block.block_type == "heading" and block.locator["slide"] == 2
    )
    assert slide_two_heading.text == "Second Title"
    assert slide_two_heading.locator["heading_path"] == ["Second Title"]

    slide_two_body = next(
        block
        for block in blocks
        if block.block_type == "paragraph" and block.locator["slide"] == 2
    )
    assert slide_two_body.text == "Body on slide two"
    assert slide_two_body.locator["heading_path"] == ["Second Title"]

    notes = next(block for block in blocks if block.block_type == "caption")
    assert notes.text == "Speaker note for slide two"
    assert notes.locator["slide"] == 2
    assert notes.metadata == {"part": "notes"}
    assert notes.locator["heading_path"] == ["Second Title"]


def test_pptx_picture_only_slide_adds_no_blocks() -> None:
    blocks = _normalized_pptx(build_picture_only_slide())
    assert [block.locator["slide"] for block in blocks] == [1, 1]
    assert all(block.locator["slide"] != 2 for block in blocks)


def test_pptx_zip_entry_limit_is_permanent() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_pptx(build_zip_entry_overflow_pptx())
    assert exc.value.code == "pptx_unreadable"
    assert str(MAX_ZIP_ENTRIES) in exc.value.message


def test_pptx_malformed_is_unreadable() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_pptx(b"PK\x03\x04not-a-valid-pptx")
    assert exc.value.code == "pptx_unreadable"


def test_empty_slides_yield_no_blocks() -> None:
    assert _normalized_pptx(build_empty_slides_deck()) == []


def test_worker_ingests_pptx_into_canonical_blocks(kb_ctx: dict) -> None:
    prepared = _enqueue(
        kb_ctx,
        build_two_slide_deck(),
        source_type="pptx",
        filename="deck.pptx",
    )
    _execute(prepared["job_id"])
    assert _block_count(prepared["version_id"]) == 5
    events = _events(prepared["trace_id"])
    assert events[0].stage == "extract" and events[0].status == "succeeded"
    assert events[1].stage == "normalize" and events[1].status == "succeeded"
    assert events[2].stage == "chunk" and events[2].status == "succeeded"
    assert [(event.stage, event.status, event.skip_reason) for event in events[3:]] == list(
        INDEX_STAGE_TAIL
    )