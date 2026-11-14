"""DOCX extraction via python-docx."""

from __future__ import annotations

import io
import zipfile

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.domain.extraction import ExtractionError, RawBlock

MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024

def _docx_unreadable(message: str) -> ExtractionError:
    return ExtractionError("docx_unreadable", message)


def _scan_zip(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise _docx_unreadable("The DOCX package could not be read or is malformed.") from exc
    except OSError as exc:
        raise _docx_unreadable("The DOCX package could not be read or is malformed.") from exc

    if len(entries) > MAX_ZIP_ENTRIES:
        raise _docx_unreadable(
            f"The DOCX package exceeds the {MAX_ZIP_ENTRIES}-entry limit."
        )
    total_size = sum(info.file_size for info in entries)
    if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise _docx_unreadable("The DOCX package exceeds the compressed-content size limit.")


def _heading_level(paragraph: Paragraph) -> int | None:
    name = (paragraph.style.name or "").strip()
    if name.startswith("Heading "):
        suffix = name.removeprefix("Heading ").strip()
        if suffix.isdigit():
            level = int(suffix)
            if 1 <= level <= 6:
                return level
    return None


def _is_list_item(paragraph: Paragraph) -> bool:
    name = (paragraph.style.name or "").strip()
    if name in {"List Paragraph", "List Bullet", "List Number"}:
        return True
    p_pr = paragraph._p.pPr  # noqa: SLF001 — OOXML numbering lives on the paragraph
    if p_pr is None:
        return False
    return p_pr.numPr is not None


def _locator(heading_path: list[str]) -> dict[str, object]:
    return {
        "source_type": "docx",
        "heading_path": list(heading_path),
    }


def _paragraph_text(paragraph: Paragraph) -> str:
    return paragraph.text or ""


def _table_row_texts(table: Table) -> list[str]:
    rows: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        while cells and not cells[-1]:
            cells.pop()
        if not any(cells):
            continue
        rows.append(" | ".join(cells))
    return rows


def extract_docx(data: bytes) -> list[RawBlock]:
    _scan_zip(data)
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise _docx_unreadable("The DOCX could not be opened or is malformed.") from exc

    heading_path: list[str] = []
    blocks: list[RawBlock] = []

    body = document.element.body
    table_index = 0
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            text = _paragraph_text(paragraph)
            if not text.strip():
                continue
            level = _heading_level(paragraph)
            if level is not None:
                title = text.strip().split("\n", 1)[0].strip()
                heading_path = heading_path[: level - 1] + [title]
                blocks.append(
                    RawBlock(
                        block_type="heading",
                        text=text,
                        locator=_locator(heading_path),
                        metadata={},
                    )
                )
                continue
            block_type = "list_item" if _is_list_item(paragraph) else "paragraph"
            blocks.append(
                RawBlock(
                    block_type=block_type,
                    text=text,
                    locator=_locator(heading_path),
                    metadata={},
                )
            )
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            for row_index, row_text in enumerate(_table_row_texts(table)):
                if not row_text.strip():
                    continue
                blocks.append(
                    RawBlock(
                        block_type="table",
                        text=row_text,
                        locator=_locator(heading_path),
                        metadata={"table_index": table_index, "row_index": row_index},
                    )
                )
            table_index += 1

    return blocks