"""Plain-text paragraphs split on blank lines."""

from __future__ import annotations

from app.domain.extraction import RawBlock


def extract_txt(text: str) -> list[RawBlock]:
    blocks: list[RawBlock] = []
    buffer: list[str] = []
    start_line: int | None = None

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        if start_line is None or not buffer:
            buffer = []
            start_line = None
            return
        body = "\n".join(buffer)
        if body.strip():
            blocks.append(
                RawBlock(
                    block_type="paragraph",
                    text=body,
                    locator={
                        "source_type": "txt",
                        "start_line": start_line,
                        "end_line": end_line,
                    },
                    metadata={},
                )
            )
        buffer = []
        start_line = None

    lines = text.split("\n")
    for index, line in enumerate(lines, start=1):
        if line.strip() == "":
            flush(index - 1)
            continue
        if start_line is None:
            start_line = index
        buffer.append(line)
    flush(len(lines))
    return blocks
