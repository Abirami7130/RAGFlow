"""CSV row extraction with the stdlib csv module."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from app.domain.extraction import ExtractionError, RawBlock

_SNIFF_BYTES = 8192
_SNIFF_DELIMITERS = ",\t;"


def _csv_unreadable(message: str) -> ExtractionError:
    return ExtractionError("csv_unreadable", message)


def _sheet_name(filename: str | None) -> str:
    if filename and filename.strip():
        return Path(filename).name
    return "data.csv"


def _dialect(text: str) -> csv.Dialect:
    sample = text[:_SNIFF_BYTES]
    if not sample.strip():
        return csv.excel
    try:
        return csv.Sniffer().sniff(sample, delimiters=_SNIFF_DELIMITERS)
    except csv.Error:
        return csv.excel


def _parse_records(text: str) -> tuple[list[list[str]], list[int]]:
    stream = io.StringIO(text)
    reader = csv.reader(stream, dialect=_dialect(text))
    rows: list[list[str]] = []
    starts: list[int] = []
    while True:
        offset = stream.tell()
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise _csv_unreadable("The CSV could not be parsed.") from exc
        rows.append(row)
        starts.append(text.count("\n", 0, offset) + 1)
    return rows, starts


def _row_nonempty(row: list[str]) -> bool:
    return any(cell.strip() for cell in row)


def _row_text(headers: list[str], values: list[str]) -> str:
    lines: list[str] = []
    for index, header in enumerate(headers):
        if index >= len(values):
            break
        value = values[index]
        if not value.strip():
            continue
        header_label = header.strip() or f"column_{index + 1}"
        lines.append(f"{header_label}: {value}")
    return "\n".join(lines)


def _locator(*, sheet: str, row_number: int) -> dict[str, object]:
    return {
        "source_type": "csv",
        "sheet": sheet,
        "row": row_number,
    }


def extract_csv(text: str, *, filename: str | None = None) -> list[RawBlock]:
    rows, starts = _parse_records(text)
    if not rows:
        return []

    header_index: int | None = None
    headers: list[str] = []
    for index, row in enumerate(rows):
        if _row_nonempty(row):
            header_index = index
            headers = [cell.strip() for cell in row]
            break

    if header_index is None:
        return []

    sheet = _sheet_name(filename)
    blocks: list[RawBlock] = []
    for row_index in range(header_index + 1, len(rows)):
        row = rows[row_index]
        if not _row_nonempty(row):
            continue
        text = _row_text(headers, row)
        if not text.strip():
            continue
        blocks.append(
            RawBlock(
                block_type="table",
                text=text,
                locator=_locator(sheet=sheet, row_number=starts[row_index]),
                metadata={"headers": headers},
            )
        )
    return blocks