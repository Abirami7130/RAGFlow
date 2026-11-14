"""Unit and worker tests for XLSX extraction."""

from __future__ import annotations

import pytest

pytest_plugins = ["test_canonical_blocks"]

from app.domain.extraction import ExtractionError
from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks
from app.modules.ingestion.extraction.xlsx import MAX_ZIP_ENTRIES, extract_xlsx
from tests.xlsx_fixtures import (
    build_blank_row_workbook,
    build_formula_workbook,
    build_headerless_workbook,
    build_hidden_sheet_workbook,
    build_multi_sheet_workbook,
    build_zip_entry_overflow_xlsx,
)

from tests.test_canonical_blocks import (  # noqa: PLC0415 — shared harness
        INDEX_STAGE_TAIL,
    _block_count,
    _enqueue,
    _events,
    _execute,
)


def _normalized_xlsx(data: bytes):
    return normalize_blocks(extract_raw_blocks("xlsx", data))


def test_xlsx_sheet_order_and_row_locators() -> None:
    blocks = _normalized_xlsx(build_multi_sheet_workbook())
    assert [(block.locator["sheet"], block.locator["row"], block.text) for block in blocks] == [
        ("Alpha", 2, "name: widget\namount: 10"),
        ("Beta", 2, "item: bolt\ncount: 4"),
    ]
    assert all("cell" not in block.locator for block in blocks)


def test_xlsx_skips_string_header_row() -> None:
    blocks = _normalized_xlsx(build_multi_sheet_workbook())
    assert all(block.locator["row"] != 1 for block in blocks)
    assert blocks[0].metadata["headers"] == ["name", "amount"]


def test_xlsx_uncached_formula_marks_metadata() -> None:
    blocks = _normalized_xlsx(build_formula_workbook())
    literal = next(block for block in blocks if "literal" in block.text)
    formula = next(block for block in blocks if "formula" in block.text)
    assert literal.text == "metric: literal\nvalue: 2"
    assert formula.text == "metric: formula\nvalue: =SUM(9,9)"
    assert formula.metadata.get("formula") is True
    assert literal.metadata.get("formula") is not True


def test_xlsx_hidden_sheet_blocks_marked() -> None:
    blocks = _normalized_xlsx(build_hidden_sheet_workbook())
    hidden = next(block for block in blocks if block.locator["sheet"] == "Hidden")
    assert hidden.metadata.get("hidden") is True
    assert hidden.text == "secret: token\nlevel: 7"


def test_xlsx_skips_blank_rows() -> None:
    blocks = _normalized_xlsx(build_blank_row_workbook())
    assert [block.locator["row"] for block in blocks] == [2, 4]


def test_xlsx_headerless_rows_use_column_letters() -> None:
    blocks = _normalized_xlsx(build_headerless_workbook())
    assert blocks[0].text == "A: 101\nB: beta"
    assert blocks[0].locator["row"] == 1


def test_xlsx_zip_entry_limit_is_permanent() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_xlsx(build_zip_entry_overflow_xlsx())
    assert exc.value.code == "xlsx_unreadable"
    assert str(MAX_ZIP_ENTRIES) in exc.value.message


def test_xlsx_malformed_is_unreadable() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_xlsx(b"PK\x03\x04not-a-valid-xlsx")
    assert exc.value.code == "xlsx_unreadable"


def test_worker_ingests_xlsx_into_canonical_blocks(kb_ctx: dict) -> None:
    prepared = _enqueue(
        kb_ctx,
        build_multi_sheet_workbook(),
        source_type="xlsx",
        filename="inventory.xlsx",
    )
    _execute(prepared["job_id"])
    assert _block_count(prepared["version_id"]) == 2
    events = _events(prepared["trace_id"])
    assert events[0].stage == "extract" and events[0].status == "succeeded"
    assert events[1].stage == "normalize" and events[1].status == "succeeded"
    assert events[2].stage == "chunk" and events[2].status == "succeeded"
    assert [(event.stage, event.status, event.skip_reason) for event in events[3:]] == list(
        INDEX_STAGE_TAIL
    )