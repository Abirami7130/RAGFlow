"""In-repo Markdown subset parser. Malformed fences and tables become paragraphs."""

from __future__ import annotations

import html
import re

from app.domain.extraction import RawBlock

_FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*([^`~]*)$")
_FENCE_CLOSE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*$")
_ATX = re.compile(r"^(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_SETEXT = re.compile(r"^[ \t]*([=-]+)[ \t]*$")
_LIST = re.compile(r"^[ \t]{0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?(.*)$")
_THEMATIC = re.compile(r"^[ \t]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})[ \t]*$")
_SCRIPT_PAIR = re.compile(r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>", re.I)
_SCRIPT_OPEN = re.compile(r"<\s*(script|style)\b", re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TAG = re.compile(r"<[^>\n]*>")
_TRAILING_HASHES = re.compile(r"[ \t]+#+\s*$")
_DELIM_CELL = re.compile(r"^:?-{3,}:?$")
_CONTINUATION = re.compile(r"^[ \t]{2,}\S")

Line = tuple[int, str]
Stack = list[tuple[int, str]]


def extract_markdown(text: str) -> list[RawBlock]:
    masked = _mask_script_and_style(text)
    lines = list(enumerate(masked.split("\n"), start=1))
    return _parse(lines, quote=False, stack=[])


def visible_text(value: str) -> str:
    value = _COMMENT.sub("", value)
    value = _TAG.sub("", value)
    return html.unescape(value)


def _open_fence(line: str) -> tuple[str, int, str] | None:
    match = _FENCE_OPEN.match(line)
    if match is None:
        return None
    token = match.group(1)
    return token[0], len(token), match.group(2).strip()


def _is_close_fence(line: str, char: str, length: int) -> bool:
    match = _FENCE_CLOSE.match(line)
    if match is None:
        return False
    token = match.group(1)
    return token[0] == char and len(token) >= length


def _close_index(lines: list[Line], start: int, char: str, length: int) -> int | None:
    for index in range(start, len(lines)):
        if _is_close_fence(lines[index][1], char, length):
            return index
    return None


def _setext_level(line: str) -> int | None:
    if "|" in line:
        return None
    match = _SETEXT.match(line)
    if match is None:
        return None
    token = match.group(1)
    if set(token) == {"="}:
        return 1
    if set(token) == {"-"}:
        return 2
    return None


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return []
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_delimiter_row(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(_DELIM_CELL.match(cell) for cell in cells)


def _is_table_start(line: str, nxt: str | None) -> bool:
    return nxt is not None and "|" in line and _is_delimiter_row(nxt) and bool(_cells(line))


def _path(stack: Stack) -> list[str]:
    return [text for _, text in stack]


def _push(stack: Stack, level: int, text: str) -> list[str]:
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, text))
    return _path(stack)


def _locator(start_line: int, end_line: int, path: list[str]) -> dict[str, object]:
    return {
        "source_type": "md",
        "start_line": start_line,
        "end_line": end_line,
        "heading_path": list(path),
    }


def _metadata(quote: bool, extra: dict[str, object] | None = None) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if extra:
        metadata.update(extra)
    if quote:
        metadata["quote"] = True
    return metadata


def _mask_script_and_style(text: str) -> str:
    lines = text.split("\n")
    masked: list[str] = []
    ignoring: str | None = None
    in_fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        if ignoring is not None:
            masked.append("")
            if re.search(rf"<\s*/\s*{ignoring}\s*>", line, re.I):
                ignoring = None
            continue
        if in_fence is not None:
            masked.append(line)
            if _is_close_fence(line, in_fence[0], in_fence[1]):
                in_fence = None
            continue
        opened = _open_fence(line)
        if opened is not None and _close_index(list(enumerate(lines, start=1)), index + 1, opened[0], opened[1]) is not None:
            in_fence = (opened[0], opened[1])
            masked.append(line)
            continue
        cleaned = _SCRIPT_PAIR.sub("", line)
        match = _SCRIPT_OPEN.search(cleaned)
        if match is not None:
            ignoring = match.group(1).lower()
            cleaned = cleaned[: match.start()]
        masked.append(cleaned)
    return "\n".join(masked)


def _structural(lines: list[Line], index: int) -> bool:
    line = lines[index][1]
    if line.strip() == "":
        return True
    opened = _open_fence(line)
    if opened is not None and _close_index(lines, index + 1, opened[0], opened[1]) is not None:
        return True
    if _ATX.match(line) or _QUOTE.match(line) or _LIST.match(line) or _THEMATIC.match(line):
        return True
    nxt = lines[index + 1][1] if index + 1 < len(lines) else None
    if _is_table_start(line, nxt):
        return True
    if nxt is not None and line.strip() and _setext_level(nxt) is not None:
        return True
    return False


def _parse(lines: list[Line], *, quote: bool, stack: Stack) -> list[RawBlock]:
    blocks: list[RawBlock] = []
    index = 0
    total = len(lines)
    while index < total:
        lineno, line = lines[index]
        if line.strip() == "":
            index += 1
            continue
        opened = _open_fence(line)
        if opened is not None:
            close = _close_index(lines, index + 1, opened[0], opened[1])
            if close is not None:
                body = "\n".join(item[1] for item in lines[index + 1 : close])
                extra: dict[str, object] = {}
                language = opened[2].split()[0] if opened[2] else ""
                if language:
                    extra["language"] = language
                blocks.append(
                    RawBlock(
                        block_type="code",
                        text=body,
                        locator=_locator(lineno, lines[close][0], _path(stack)),
                        metadata=_metadata(quote, extra),
                    )
                )
                index = close + 1
                continue
        atx = _ATX.match(line)
        if atx is not None:
            raw = _TRAILING_HASHES.sub("", (atx.group(2) or "")).strip()
            text = visible_text(raw).strip()
            if text:
                path = _push(stack, len(atx.group(1)), text)
                blocks.append(
                    RawBlock(
                        block_type="heading",
                        text=text,
                        locator=_locator(lineno, lineno, path),
                        metadata=_metadata(quote),
                    )
                )
            index += 1
            continue
        if _QUOTE.match(line):
            gathered: list[Line] = []
            while index < total and _QUOTE.match(lines[index][1]):
                inner = _QUOTE.match(lines[index][1])
                gathered.append((lines[index][0], inner.group(1) if inner else ""))
                index += 1
            blocks.extend(_parse(gathered, quote=True, stack=stack))
            continue
        nxt = lines[index + 1][1] if index + 1 < total else None
        if _is_table_start(line, nxt):
            body: list[RawBlock] = []
            scan = index + 2
            while scan < total and lines[scan][1].strip() and "|" in lines[scan][1] and not _is_delimiter_row(lines[scan][1]):
                cells = [visible_text(cell).strip() for cell in _cells(lines[scan][1])]
                text = " | ".join(cell for cell in cells if cell)
                if text:
                    body.append(
                        RawBlock(
                            block_type="table",
                            text=text,
                            locator=_locator(lines[scan][0], lines[scan][0], _path(stack)),
                            metadata=_metadata(quote),
                        )
                    )
                scan += 1
            if body:
                blocks.extend(body)
                index = scan
                continue
        listed = _LIST.match(line)
        if listed is not None:
            text = visible_text(listed.group(1)).strip()
            end_line = lineno
            index += 1
            while index < total and _CONTINUATION.match(lines[index][1]) and _LIST.match(lines[index][1]) is None:
                extra_line = visible_text(lines[index][1].strip()).strip()
                if extra_line:
                    text = f"{text}\n{extra_line}" if text else extra_line
                end_line = lines[index][0]
                index += 1
            if text:
                blocks.append(
                    RawBlock(
                        block_type="list_item",
                        text=text,
                        locator=_locator(lineno, end_line, _path(stack)),
                        metadata=_metadata(quote),
                    )
                )
            continue
        level = _setext_level(nxt) if nxt is not None else None
        if level is not None and line.strip():
            text = visible_text(line).strip()
            if text:
                path = _push(stack, level, text)
                blocks.append(
                    RawBlock(
                        block_type="heading",
                        text=text,
                        locator=_locator(lineno, lines[index + 1][0], path),
                        metadata=_metadata(quote),
                    )
                )
            index += 2
            continue
        if _THEMATIC.match(line):
            index += 1
            continue
        start_line = lineno
        chunk = [line]
        end_line = lineno
        index += 1
        while index < total and not _structural(lines, index):
            chunk.append(lines[index][1])
            end_line = lines[index][0]
            index += 1
        text = visible_text("\n".join(chunk)).strip()
        if text:
            blocks.append(
                RawBlock(
                    block_type="paragraph",
                    text=text,
                    locator=_locator(start_line, end_line, _path(stack)),
                    metadata=_metadata(quote),
                )
            )
    return blocks
