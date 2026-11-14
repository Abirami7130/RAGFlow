"""Unit tests for TXT and Markdown extraction and normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.extraction import ExtractionError, RawBlock
from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"


def _normalized(source_type: str, data: bytes):
    return normalize_blocks(extract_raw_blocks(source_type, data))


def test_txt_fixture_splits_paragraphs_without_headings() -> None:
    blocks = _normalized("txt", (FIXTURES / "sample.txt").read_bytes())
    assert [(block.sequence, block.block_type, block.text) for block in blocks] == [
        (0, "paragraph", "Alpha paragraph\nstill alpha"),
        (1, "paragraph", "# Not a heading"),
        (2, "paragraph", "Beta paragraph"),
    ]
    assert blocks[0].locator == {"source_type": "txt", "start_line": 1, "end_line": 2}
    assert blocks[1].locator == {"source_type": "txt", "start_line": 4, "end_line": 4}
    assert blocks[2].locator == {"source_type": "txt", "start_line": 6, "end_line": 6}
    assert all(block.metadata == {} for block in blocks)
    assert all("char_start" not in block.locator and "heading_path" not in block.locator for block in blocks)


def test_markdown_fixture_covers_the_subset() -> None:
    blocks = _normalized("md", (FIXTURES / "sample.md").read_bytes())
    assert [(block.block_type, block.text, block.locator["heading_path"], block.metadata) for block in blocks] == [
        ("heading", "Title", ["Title"], {}),
        ("paragraph", "Intro with bold text.", ["Title"], {}),
        ("heading", "Details", ["Title", "Details"], {}),
        ("list_item", "first item", ["Title", "Details"], {}),
        ("list_item", "second item", ["Title", "Details"], {}),
        ("paragraph", "quoted text", ["Title", "Details"], {"quote": True}),
        ("code", 'print("hi")', ["Title", "Details"], {"language": "python"}),
        ("table", "A | 1", ["Title", "Details"], {}),
        ("table", "B | 2", ["Title", "Details"], {}),
        ("heading", "Setext Title", ["Setext Title"], {}),
        ("paragraph", "After script.", ["Setext Title"], {}),
    ]
    assert all("secret" not in block.text for block in blocks)
    assert blocks[0].locator["start_line"] == 1 and blocks[0].locator["end_line"] == 1
    assert blocks[6].locator["start_line"] == 12 and blocks[6].locator["end_line"] == 14
    assert blocks[7].locator["start_line"] == blocks[7].locator["end_line"] == 18
    assert [block.sequence for block in blocks] == list(range(len(blocks)))
    assert all(set(block.locator) == {"source_type", "start_line", "end_line", "heading_path"} for block in blocks)


def test_markdown_structure_edges() -> None:
    quoted = _normalized("md", b"> # Quoted\n>\n> body\n")
    assert [(block.block_type, block.text, block.metadata) for block in quoted] == [
        ("heading", "Quoted", {"quote": True}),
        ("paragraph", "body", {"quote": True}),
    ]
    assert quoted[1].locator["heading_path"] == ["Quoted"]

    fenced = _normalized("md", b"```\nkept\n```\n")
    assert fenced[0].block_type == "code"
    assert fenced[0].metadata == {}
    assert fenced[0].text == "kept"

    code_with_script = _normalized("md", b"```\n<script>secret()</script>\n```\n")
    assert "secret()" in code_with_script[0].text

    unclosed = _normalized("md", b"```python\nhello\n")
    assert unclosed[0].block_type == "paragraph"
    assert all(block.block_type != "code" for block in unclosed)

    malformed_table = _normalized("md", b"| a | b |\n| nope |\n")
    assert all(block.block_type != "table" for block in malformed_table)
    assert "a" in malformed_table[0].text

    html = _normalized("md", b"<h1>Nope</h1>\n<p>Hello</p>\n")
    assert [block.block_type for block in html] == ["paragraph"]
    assert html[0].text == "Nope\nHello"
    assert "secret" not in _normalized("md", b"visible <style>secret</style> tail\n")[0].text


def test_decoding_and_normalization_rules() -> None:
    hello = "Hello\n\nWorld"
    variants = [
        hello.encode("utf-8-sig"),
        b"\xff\xfe" + hello.encode("utf-16-le"),
        b"\xfe\xff" + hello.encode("utf-16-be"),
    ]
    assert all([(block.text) for block in _normalized("txt", data)] == ["Hello", "World"] for data in variants)

    crlf = _normalized("txt", b"A\r\n\r\nB")
    assert [block.text for block in crlf] == ["A", "B"]
    assert "\r" not in crlf[0].text
    assert crlf[1].locator["start_line"] == 3

    cleaned = _normalized("txt", b"A\x01B\n\n\x02\n\nC\tD")
    assert [(block.sequence, block.text) for block in cleaned] == [(0, "AB"), (1, "C\tD")]

    with pytest.raises(ExtractionError) as invalid:
        extract_raw_blocks("txt", b"\x80\x81")
    assert invalid.value.code == "invalid_encoding"

    with pytest.raises(ExtractionError) as nul:
        extract_raw_blocks("txt", b"a\x00b")
    assert nul.value.code == "nul_in_text"

    assert _normalized("txt", b" \n\t\n ") == []


def test_raw_block_contract_is_in_memory() -> None:
    block = RawBlock(block_type="paragraph", text="a", locator={"source_type": "txt"}, metadata={})
    assert block.text == "a"
