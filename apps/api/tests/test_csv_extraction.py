"""Unit and worker tests for CSV extraction."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

pytest_plugins = ["test_canonical_blocks"]

from app.domain.extraction import ExtractionError
from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks
from app.modules.ingestion.extraction.csv import extract_csv

from tests.test_canonical_blocks import (  # noqa: PLC0415 — shared harness
        INDEX_STAGE_TAIL,
    _block_count,
    _enqueue,
    _events,
    _execute,
)

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"


def _normalized_csv(data: bytes, *, filename: str | None = "sample.csv"):
    return normalize_blocks(extract_raw_blocks("csv", data, filename=filename))


def test_csv_fixture_row_locators_and_header_text() -> None:
    blocks = _normalized_csv((FIXTURES / "sample.csv").read_bytes(), filename="sample.csv")
    assert [(block.locator["row"], block.text.split("\n")[0]) for block in blocks] == [
        (2, "name: widget"),
        (3, "name: gadget"),
        (4, "name: gizmo"),
    ]
    assert all(block.locator["sheet"] == "sample.csv" for block in blocks)
    assert all(block.block_type == "table" for block in blocks)
    assert blocks[0].metadata["headers"] == ["name", "amount"]
    assert "amount: 10" in blocks[0].text
    assert blocks[1].text == "name: gadget\namount: 12,50"
    assert "amount: multi" in blocks[2].text and "line" in blocks[2].text


def test_csv_sniffs_tab_delimiter() -> None:
    blocks = _normalized_csv(b"id\tvalue\n1\tone\n2\ttwo\n", filename="data.tsv")
    assert len(blocks) == 2
    assert blocks[0].locator == {"source_type": "csv", "sheet": "data.tsv", "row": 2}
    assert blocks[0].text == "id: 1\nvalue: one"


def test_csv_utf8_bom_decodes() -> None:
    blocks = _normalized_csv(b"\xef\xbb\xbfkey,val\nk,v\n", filename="bom.csv")
    assert blocks[0].metadata["headers"] == ["key", "val"]


def test_csv_omits_header_block_and_empty_values() -> None:
    blocks = _normalized_csv(b"a,b,c\nx,,z\n", filename="sparse.csv")
    assert len(blocks) == 1
    assert blocks[0].locator["row"] == 2
    assert blocks[0].text == "a: x\nc: z"


def test_csv_header_only_yields_no_blocks() -> None:
    assert _normalized_csv(b"name,value\n", filename="headers-only.csv") == []


def test_csv_invalid_encoding_is_permanent() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_raw_blocks("csv", b"\x80\x81", filename="bad.csv")
    assert exc.value.code == "invalid_encoding"


def test_csv_malformed_raises_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenReader:
        def __iter__(self):
            return self

        def __next__(self):
            raise csv.Error("broken")

    monkeypatch.setattr(csv, "reader", lambda *args, **kwargs: _BrokenReader())
    with pytest.raises(ExtractionError) as exc:
        extract_csv("a,b\n1,2\n", filename="broken.csv")
    assert exc.value.code == "csv_unreadable"


def test_worker_ingests_csv_into_canonical_blocks(kb_ctx: dict) -> None:
    data = (FIXTURES / "sample.csv").read_bytes()
    prepared = _enqueue(kb_ctx, data, source_type="csv", filename="sample.csv")
    _execute(prepared["job_id"])
    assert _block_count(prepared["version_id"]) == 3
    events = _events(prepared["trace_id"])
    assert events[0].stage == "extract" and events[0].status == "succeeded"
    assert events[1].stage == "normalize" and events[1].status == "succeeded"
    assert events[2].stage == "chunk" and events[2].status == "succeeded"
    assert [(event.stage, event.status, event.skip_reason) for event in events[3:]] == list(
        INDEX_STAGE_TAIL
    )


def test_worker_header_only_csv_fails_empty_content(kb_ctx: dict) -> None:
    from app.core.db import SessionLocal
    from app.models import Document, Job, ProcessingRun

    prepared = _enqueue(kb_ctx, b"h1,h2\n", source_type="csv", filename="empty.csv")
    _execute(prepared["job_id"])
    assert _block_count(prepared["version_id"]) == 0
    with SessionLocal() as session:
        job = session.get(Job, prepared["job_id"])
        run = session.get(ProcessingRun, prepared["run_id"])
        assert job is not None and run is not None
        assert job.last_error_code == "empty_content"
        assert run.error_code == "empty_content"