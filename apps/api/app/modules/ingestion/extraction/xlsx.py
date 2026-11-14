"""XLSX extraction via openpyxl."""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, time

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils import get_column_letter
from openpyxl.workbook.workbook import Workbook

from app.domain.extraction import ExtractionError, RawBlock

MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


def _xlsx_unreadable(message: str) -> ExtractionError:
    return ExtractionError("xlsx_unreadable", message)


def _scan_zip(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise _xlsx_unreadable("The XLSX package could not be read or is malformed.") from exc
    except OSError as exc:
        raise _xlsx_unreadable("The XLSX package could not be read or is malformed.") from exc

    if len(entries) > MAX_ZIP_ENTRIES:
        raise _xlsx_unreadable(f"The XLSX package exceeds the {MAX_ZIP_ENTRIES}-entry limit.")
    total_size = sum(info.file_size for info in entries)
    if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise _xlsx_unreadable("The XLSX package exceeds the compressed-content size limit.")


def _open_workbooks(data: bytes) -> tuple[Workbook, Workbook]:
    try:
        formulas = load_workbook(io.BytesIO(data), data_only=False, read_only=False)
        values = load_workbook(io.BytesIO(data), data_only=True, read_only=False)
    except Exception as exc:
        raise _xlsx_unreadable("The XLSX could not be opened or is malformed.") from exc
    return formulas, values


def _format_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep="T", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    return str(value).strip()


def _row_values(
    formula_row: tuple[Cell, ...],
    value_row: tuple[Cell, ...],
) -> tuple[list[tuple[int, str, object]], bool]:
    cells: list[tuple[int, str, object]] = []
    has_uncached_formula = False
    for index, formula_cell in enumerate(formula_row):
        value_cell = value_row[index] if index < len(value_row) else None
        column_index = formula_cell.column - 1
        label = get_column_letter(formula_cell.column)
        if formula_cell.data_type == "f" or (
            isinstance(formula_cell.value, str) and formula_cell.value.startswith("=")
        ):
            cached = value_cell.value if value_cell is not None else None
            if cached is None:
                cells.append((column_index, label, formula_cell.value))
                has_uncached_formula = True
            else:
                cells.append((column_index, label, cached))
            continue
        cells.append((column_index, label, formula_cell.value))
    return cells, has_uncached_formula


def _entirely_string_header(row: tuple[Cell, ...]) -> list[str] | None:
    values = [cell.value for cell in row]
    nonempty = [value for value in values if _format_value(value)]
    if not nonempty:
        return None
    if not all(isinstance(value, str) for value in nonempty):
        return None
    return [str(value).strip() if value is not None else "" for value in values]


def _locator(*, sheet: str, row_number: int) -> dict[str, object]:
    return {
        "source_type": "xlsx",
        "sheet": sheet,
        "row": row_number,
    }


def _row_text(headers: list[str] | None, cells: list[tuple[int, str, object]]) -> str:
    lines: list[str] = []
    for column_index, default_label, value in cells:
        text = _format_value(value)
        if not text:
            continue
        if headers is not None and column_index < len(headers) and headers[column_index]:
            label = headers[column_index]
        else:
            label = default_label
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def _row_nonempty(cells: list[tuple[int, str, object]]) -> bool:
    return any(_format_value(value) for _, _, value in cells)


def extract_xlsx(data: bytes) -> list[RawBlock]:
    _scan_zip(data)
    formulas_book, values_book = _open_workbooks(data)
    blocks: list[RawBlock] = []

    try:
        for sheet in formulas_book.worksheets:
            value_sheet = values_book[sheet.title]
            hidden = sheet.sheet_state in {"hidden", "veryHidden"}
            max_row = sheet.max_row or 0
            max_column = sheet.max_column or 0
            if max_row == 0 or max_column == 0:
                continue

            first_row = tuple(sheet.iter_rows(min_row=1, max_row=1, max_col=max_column, values_only=False))
            headers = _entirely_string_header(first_row[0]) if first_row else None
            start_row = 2 if headers is not None else 1

            for row_number in range(start_row, max_row + 1):
                formula_row = tuple(
                    sheet.iter_rows(min_row=row_number, max_row=row_number, max_col=max_column, values_only=False)
                )[0]
                value_row = tuple(
                    value_sheet.iter_rows(
                        min_row=row_number,
                        max_row=row_number,
                        max_col=max_column,
                        values_only=False,
                    )
                )[0]
                cells, has_uncached_formula = _row_values(formula_row, value_row)
                if not _row_nonempty(cells):
                    continue
                text = _row_text(headers, cells)
                if not text.strip():
                    continue
                metadata: dict[str, object] = {}
                if headers is not None:
                    metadata["headers"] = headers
                if hidden:
                    metadata["hidden"] = True
                if has_uncached_formula:
                    metadata["formula"] = True
                blocks.append(
                    RawBlock(
                        block_type="table",
                        text=text,
                        locator=_locator(sheet=sheet.title, row_number=row_number),
                        metadata=metadata,
                    )
                )
    finally:
        formulas_book.close()
        values_book.close()

    return blocks