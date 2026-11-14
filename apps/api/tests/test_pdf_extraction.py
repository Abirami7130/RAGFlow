"""Unit and worker tests for PDF extraction."""

from __future__ import annotations

import pytest

pytest_plugins = ["test_canonical_blocks"]

from app.domain.extraction import ExtractionError
from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks
from app.modules.ingestion.extraction.pdf import MAX_PDF_PAGES, extract_pdf
from tests.pdf_fixtures import (
    build_encrypted_pdf,
    build_image_only_pdf,
    build_multi_page_pdf,
    build_pdf_bytes,
    build_pdf_with_sizes,
)

from tests.test_canonical_blocks import (  # noqa: PLC0415 — shared harness
        INDEX_STAGE_TAIL,
    _add_version,
    _block_count,
    _enqueue,
    _events,
    _execute,
    _reprocess,
)


def _normalized_pdf(data: bytes):
    return normalize_blocks(extract_raw_blocks("pdf", data))


def test_pdf_multi_page_blocks_carry_page_and_bbox() -> None:
    data = build_pdf_bytes("Alpha on one", "Beta on two")
    blocks = _normalized_pdf(data)
    assert [(block.text, block.locator["page"]) for block in blocks] == [
        ("Alpha on one", 1),
        ("Beta on two", 2),
    ]
    for block in blocks:
        assert block.locator["source_type"] == "pdf"
        bbox = block.locator["bbox"]
        assert bbox["x0"] <= 72.0 <= bbox["x1"]
        assert bbox["y0"] <= 72.0 <= bbox["y1"]
        assert block.block_type == "paragraph"


def test_pdf_heading_path_from_font_size() -> None:
    data = build_pdf_with_sizes(
        [
            ("Section", 18.0),
            ("Body copy", 11.0),
        ]
    )
    blocks = _normalized_pdf(data)
    assert blocks[0].block_type == "heading"
    assert blocks[0].text == "Section"
    assert blocks[0].locator["heading_path"] == ["Section"]
    assert blocks[1].block_type == "paragraph"
    assert blocks[1].locator["heading_path"] == ["Section"]


def test_pdf_page_limit_is_permanent() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_pdf(build_multi_page_pdf(MAX_PDF_PAGES + 1))
    assert exc.value.code == "pdf_unreadable"
    assert "500" in exc.value.message


def test_pdf_encrypted_is_unreadable() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_pdf(build_encrypted_pdf())
    assert exc.value.code == "pdf_unreadable"


def test_pdf_malformed_is_unreadable() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_pdf(b"%PDF-1.4\nnot a real object graph\n%%EOF")
    assert exc.value.code == "pdf_unreadable"


def test_pdf_image_only_is_unreadable() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract_pdf(build_image_only_pdf())
    assert exc.value.code == "pdf_unreadable"


def test_worker_ingests_pdf_into_canonical_blocks(kb_ctx: dict) -> None:
    prepared = _enqueue(kb_ctx, build_pdf_bytes("Hello PDF"), source_type="pdf", filename="note.pdf")
    _execute(prepared["job_id"])
    assert _block_count(prepared["version_id"]) == 1
    events = _events(prepared["trace_id"])
    assert events[0].stage == "extract" and events[0].status == "succeeded"
    assert events[1].stage == "normalize" and events[1].status == "succeeded"
    assert events[2].stage == "chunk" and events[2].status == "succeeded"
    assert [(event.stage, event.status, event.skip_reason) for event in events[3:]] == list(
        INDEX_STAGE_TAIL
    )


def test_pdf_reprocess_with_existing_blocks(kb_ctx: dict) -> None:
    from app.core.db import SessionLocal
    from app.models import Document, ProcessingRun

    prepared = _enqueue(kb_ctx, build_pdf_bytes("Stable text"), source_type="pdf", filename="stable.pdf")
    _execute(prepared["job_id"])
    before = _block_count(prepared["version_id"])

    replacement = _add_version(
        kb_ctx,
        build_pdf_bytes("Replacement page"),
        source_type="pdf",
        filename="next.pdf",
        number=2,
    )
    _execute(replacement["job_id"])
    replay = _reprocess(kb_ctx, prepared["version_id"])
    _execute(replay["job_id"])

    assert _block_count(prepared["version_id"]) == before
    with SessionLocal() as session:
        doc = session.get(Document, kb_ctx["document_id"])
        assert doc is not None
        assert doc.status == "ready"
        assert doc.current_version_id in (prepared["version_id"], replacement["version_id"])
        assert doc.preview_version_id is None