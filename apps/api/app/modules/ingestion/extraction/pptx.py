"""PPTX extraction via python-pptx."""

from __future__ import annotations

import io
import zipfile

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.shapes.base import BaseShape

from app.domain.extraction import ExtractionError, RawBlock

MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


def _pptx_unreadable(message: str) -> ExtractionError:
    return ExtractionError("pptx_unreadable", message)


def _scan_zip(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise _pptx_unreadable("The PPTX package could not be read or is malformed.") from exc
    except OSError as exc:
        raise _pptx_unreadable("The PPTX package could not be read or is malformed.") from exc

    if len(entries) > MAX_ZIP_ENTRIES:
        raise _pptx_unreadable(f"The PPTX package exceeds the {MAX_ZIP_ENTRIES}-entry limit.")
    total_size = sum(info.file_size for info in entries)
    if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise _pptx_unreadable("The PPTX package exceeds the compressed-content size limit.")


def _locator(slide_number: int, heading_path: list[str]) -> dict[str, object]:
    return {
        "source_type": "pptx",
        "slide": slide_number,
        "heading_path": list(heading_path),
    }


def _is_title_placeholder(shape: BaseShape) -> bool:
    if not shape.is_placeholder:
        return False
    ph_type = shape.placeholder_format.type
    return ph_type in {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}


def _iter_shapes(shapes) -> list[BaseShape]:
    ordered: list[BaseShape] = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            ordered.extend(_iter_shapes(shape.shapes))
            continue
        if shape.shape_type in {MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.CHART}:
            continue
        ordered.append(shape)
    return ordered


def _table_rows(shape: BaseShape) -> list[str]:
    if not shape.has_table:
        return []
    rows: list[str] = []
    for row in shape.table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        while cells and not cells[-1]:
            cells.pop()
        if not any(cells):
            continue
        rows.append(" | ".join(cells))
    return rows


def _append_text_frame_blocks(
    *,
    shape: BaseShape,
    slide_number: int,
    heading_path: list[str],
    blocks: list[RawBlock],
    block_type: str,
    metadata: dict[str, object] | None = None,
) -> None:
    if not shape.has_text_frame:
        return
    meta = dict(metadata or {})
    for paragraph in shape.text_frame.paragraphs:
        text = paragraph.text or ""
        if not text.strip():
            continue
        blocks.append(
            RawBlock(
                block_type=block_type,
                text=text,
                locator=_locator(slide_number, heading_path),
                metadata=meta,
            )
        )


def _extract_slide_shapes(
    *,
    slide_number: int,
    shapes,
    blocks: list[RawBlock],
    heading_path: list[str],
    table_counter: list[int],
) -> list[str]:
    slide_heading = list(heading_path)
    for shape in _iter_shapes(shapes):
        if shape.shape_type == MSO_SHAPE_TYPE.TABLE:
            table_index = table_counter[0]
            for row_index, row_text in enumerate(_table_rows(shape)):
                blocks.append(
                    RawBlock(
                        block_type="table",
                        text=row_text,
                        locator=_locator(slide_number, slide_heading),
                        metadata={"table_index": table_index, "row_index": row_index},
                    )
                )
            table_counter[0] += 1
            continue

        if _is_title_placeholder(shape):
            if not shape.has_text_frame:
                continue
            title = (shape.text or "").strip()
            if not title:
                continue
            slide_heading = [title.split("\n", 1)[0].strip()]
            blocks.append(
                RawBlock(
                    block_type="heading",
                    text=shape.text,
                    locator=_locator(slide_number, slide_heading),
                    metadata={},
                )
            )
            continue

        _append_text_frame_blocks(
            shape=shape,
            slide_number=slide_number,
            heading_path=slide_heading,
            blocks=blocks,
            block_type="paragraph",
        )
    return slide_heading


def extract_pptx(data: bytes) -> list[RawBlock]:
    _scan_zip(data)
    try:
        presentation = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise _pptx_unreadable("The PPTX could not be opened or is malformed.") from exc

    blocks: list[RawBlock] = []
    table_counter = [0]

    for slide_index, slide in enumerate(presentation.slides, start=1):
        slide_heading = _extract_slide_shapes(
            slide_number=slide_index,
            shapes=slide.shapes,
            blocks=blocks,
            heading_path=[],
            table_counter=table_counter,
        )

        if slide.has_notes_slide:
            notes_slide = slide.notes_slide
            for shape in _iter_shapes(notes_slide.shapes):
                _append_text_frame_blocks(
                    shape=shape,
                    slide_number=slide_index,
                    heading_path=slide_heading,
                    blocks=blocks,
                    block_type="caption",
                    metadata={"part": "notes"},
                )

    return blocks