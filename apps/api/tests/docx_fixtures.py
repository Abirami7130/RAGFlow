"""Helpers for building DOCX bytes in tests."""

from __future__ import annotations

import io
import zipfile

from docx import Document

from app.modules.ingestion.extraction.docx import MAX_ZIP_ENTRIES


def build_heading_docx() -> bytes:
    doc = Document()
    doc.add_heading("H1", level=1)
    doc.add_paragraph("Body under H1")
    doc.add_heading("H2", level=2)
    doc.add_paragraph("Body under H2")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_docx_with_header() -> bytes:
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "Header text must not appear"
    doc.add_paragraph("Only body text")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_docx_with_list_and_table() -> bytes:
    doc = Document()
    doc.add_heading("Section", level=1)
    doc.add_paragraph("Item one", style="List Bullet")
    doc.add_paragraph("Item two", style="List Bullet")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "1"
    table.cell(1, 0).text = "B"
    table.cell(1, 1).text = "2"
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_zip_entry_overflow_docx(extra_entries: int = 1) -> bytes:
    base = build_heading_docx()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base), "r") as src, zipfile.ZipFile(out, "w") as dest:
        for item in src.infolist():
            dest.writestr(item, src.read(item.filename))
        start = len(src.infolist())
        for index in range(start, MAX_ZIP_ENTRIES + extra_entries):
            dest.writestr(f"padding/extra_{index}.txt", b"x")
    return out.getvalue()