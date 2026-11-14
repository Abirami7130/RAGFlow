"""Helpers for building XLSX bytes in tests."""

from __future__ import annotations

import io
import zipfile

from openpyxl import Workbook

from app.modules.ingestion.extraction.xlsx import MAX_ZIP_ENTRIES


def _save(workbook: Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_multi_sheet_workbook() -> bytes:
    workbook = Workbook()
    first = workbook.active
    assert first is not None
    first.title = "Alpha"
    first.append(["name", "amount"])
    first.append(["widget", 10])

    second = workbook.create_sheet("Beta", 1)
    second.append(["item", "count"])
    second.append(["bolt", 4])
    return _save(workbook)


def build_formula_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet["A1"] = "metric"
    sheet["B1"] = "value"
    sheet["A2"] = "literal"
    sheet["B2"] = 2
    sheet["A3"] = "formula"
    sheet["B3"] = "=SUM(9,9)"
    return _save(workbook)


def build_hidden_sheet_workbook() -> bytes:
    workbook = Workbook()
    visible = workbook.active
    assert visible is not None
    visible.title = "Visible"
    visible.append(["name", "qty"])
    visible.append(["gear", 3])
    hidden = workbook.create_sheet("Hidden")
    hidden.append(["secret", "level"])
    hidden.append(["token", 7])
    hidden.sheet_state = "hidden"
    return _save(workbook)


def build_blank_row_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["id", "label"])
    sheet.append([1, "one"])
    sheet.append([None, None])
    sheet.append([2, "two"])
    return _save(workbook)


def build_headerless_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet["A1"] = 101
    sheet["B1"] = "beta"
    return _save(workbook)


def build_zip_entry_overflow_xlsx(extra_entries: int = 1) -> bytes:
    base = build_multi_sheet_workbook()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base), "r") as src, zipfile.ZipFile(out, "w") as dest:
        for item in src.infolist():
            dest.writestr(item, src.read(item.filename))
        start = len(src.infolist())
        for index in range(start, MAX_ZIP_ENTRIES + extra_entries):
            dest.writestr(f"padding/extra_{index}.txt", b"x")
    return out.getvalue()