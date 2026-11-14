"""Helpers for building PPTX bytes in tests."""

from __future__ import annotations

import io
import zipfile

from pptx import Presentation
from pptx.util import Inches

from app.modules.ingestion.extraction.pptx import MAX_ZIP_ENTRIES

_MIN_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _save_presentation(presentation: Presentation) -> bytes:
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def build_two_slide_deck() -> bytes:
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[0])
    first.shapes.title.text = "First"
    first.placeholders[1].text = "Body on slide one"

    second = presentation.slides.add_slide(presentation.slide_layouts[0])
    second.shapes.title.text = "Second Title"
    second.placeholders[1].text = "Body on slide two"
    notes = second.notes_slide.notes_text_frame
    notes.text = "Speaker note for slide two"
    return _save_presentation(presentation)


def build_picture_only_slide() -> bytes:
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[0])
    first.shapes.title.text = "Has text"
    first.placeholders[1].text = "Visible body"
    blank = presentation.slides.add_slide(presentation.slide_layouts[6])
    blank.shapes.add_picture(io.BytesIO(_MIN_PNG), Inches(1), Inches(1), width=Inches(2))
    return _save_presentation(presentation)


def build_empty_slides_deck() -> bytes:
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])
    presentation.slides.add_slide(presentation.slide_layouts[6])
    return _save_presentation(presentation)


def build_zip_entry_overflow_pptx(extra_entries: int = 1) -> bytes:
    base = build_two_slide_deck()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base), "r") as src, zipfile.ZipFile(out, "w") as dest:
        for item in src.infolist():
            dest.writestr(item, src.read(item.filename))
        start = len(src.infolist())
        for index in range(start, MAX_ZIP_ENTRIES + extra_entries):
            dest.writestr(f"padding/extra_{index}.txt", b"x")
    return out.getvalue()