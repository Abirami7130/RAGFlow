"""PDF extraction via PyMuPDF."""

from __future__ import annotations

from collections import Counter

import pymupdf

from app.domain.extraction import ExtractionError, RawBlock

MAX_PDF_PAGES = 500
_HEADING_SIZE_RATIO = 1.15


def _pdf_unreadable(message: str) -> ExtractionError:
    return ExtractionError("pdf_unreadable", message)


def _open_document(data: bytes) -> pymupdf.Document:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise _pdf_unreadable("The PDF could not be opened or is malformed.") from exc
    try:
        if doc.page_count == 0:
            raise _pdf_unreadable("The PDF has no pages.")
        if doc.page_count > MAX_PDF_PAGES:
            raise _pdf_unreadable(f"The PDF exceeds the {MAX_PDF_PAGES}-page limit.")
        if doc.needs_pass and not doc.authenticate(""):
            raise _pdf_unreadable("The PDF is encrypted.")
        return doc
    except ExtractionError:
        doc.close()
        raise
    except Exception:
        doc.close()
        raise


def _span_sizes(doc: pymupdf.Document) -> list[float]:
    sizes: list[float] = []
    for page in doc:
        payload = page.get_text("dict")
        for block in payload.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        sizes.append(float(span.get("size", 0.0)))
    return sizes


def _body_font_size(sizes: list[float]) -> float:
    if not sizes:
        return 12.0
    counts = Counter(round(size, 1) for size in sizes)
    return float(min(counts, key=lambda size: (-counts[size], size)))


def _heading_levels(sizes: list[float], body: float) -> dict[float, int]:
    prominent = sorted({size for size in sizes if size >= body * _HEADING_SIZE_RATIO}, reverse=True)
    return {size: level for level, size in enumerate(prominent)}


def _merge_bbox(bbox: tuple[float, float, float, float] | None, span_bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    if bbox is None:
        return span_bbox
    x0, y0, x1, y1 = bbox
    sx0, sy0, sx1, sy1 = span_bbox
    return min(x0, sx0), min(y0, sy0), max(x1, sx1), max(y1, sy1)


def extract_pdf(data: bytes) -> list[RawBlock]:
    doc = _open_document(data)
    try:
        sizes = _span_sizes(doc)
        if not sizes:
            raise _pdf_unreadable("The PDF contains no extractable text.")
        body = _body_font_size(sizes)
        level_by_size = _heading_levels(sizes, body)
        heading_path: list[str] = []
        blocks: list[RawBlock] = []

        for page_number, page in enumerate(doc, start=1):
            payload = page.get_text("dict")
            for block in payload.get("blocks", []):
                if block.get("type") != 0:
                    continue
                parts: list[str] = []
                max_size = body
                bbox: tuple[float, float, float, float] | None = None
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text:
                            continue
                        parts.append(text)
                        size = float(span.get("size", body))
                        max_size = max(max_size, size)
                        bbox = _merge_bbox(bbox, tuple(span["bbox"]))
                text = "".join(parts)
                if not text.strip() or bbox is None:
                    continue
                is_heading = max_size >= body * _HEADING_SIZE_RATIO and max_size in level_by_size
                block_type = "heading" if is_heading else "paragraph"
                title = text.strip().split("\n", 1)[0].strip()
                path = list(heading_path)
                if is_heading:
                    level = level_by_size[max_size]
                    heading_path = heading_path[:level] + [title]
                    path = list(heading_path)
                blocks.append(
                    RawBlock(
                        block_type=block_type,
                        text=text,
                        locator={
                            "source_type": "pdf",
                            "page": page_number,
                            "bbox": {"x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3]},
                            "heading_path": path,
                        },
                        metadata={},
                    )
                )
        return blocks
    finally:
        doc.close()