"""Source-type dispatch for implemented extractors."""

from __future__ import annotations

from app.domain.extraction import ExtractionError, RawBlock
from app.modules.ingestion.extraction.csv import extract_csv
from app.modules.ingestion.extraction.decode import decode_textual
from app.modules.ingestion.extraction.docx import extract_docx
from app.modules.ingestion.extraction.json_doc import extract_json
from app.modules.ingestion.extraction.markdown import extract_markdown
from app.modules.ingestion.extraction.pdf import extract_pdf
from app.modules.ingestion.extraction.pptx import extract_pptx
from app.modules.ingestion.extraction.txt import extract_txt
from app.modules.ingestion.extraction.xlsx import extract_xlsx

_IMPLEMENTED = {
    "txt": extract_txt,
    "md": extract_markdown,
    "pdf": extract_pdf,
    "docx": extract_docx,
    "pptx": extract_pptx,
    "xlsx": extract_xlsx,
    "json": extract_json,
}
_TEXTUAL = frozenset({"txt", "md"})


def extract_raw_blocks(source_type: str, data: bytes, *, filename: str | None = None) -> list[RawBlock]:
    if source_type == "csv":
        return extract_csv(decode_textual(data), filename=filename)
    extractor = _IMPLEMENTED.get(source_type)
    if extractor is None:
        raise ExtractionError(
            "extractor_not_implemented",
            "Format extraction is not available until the extractor milestone.",
        )
    if source_type in _TEXTUAL:
        return extractor(decode_textual(data))
    return extractor(data)
