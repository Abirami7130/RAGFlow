"""HTML to canonical blocks using BeautifulSoup."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.domain.extraction import RawBlock

_DROP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "iframe",
        "form",
        "nav",
        "footer",
        "header",
        "aside",
    }
)
_HEADING = re.compile(r"^h([1-6])$", re.I)
def _locator(url: str, heading_path: list[str]) -> dict[str, object]:
    return {
        "source_type": "web",
        "url": url,
        "heading_path": list(heading_path),
    }


def _visible_text(element: Tag) -> str:
    return element.get_text("\n", strip=True)


def discover_links(html: bytes, *, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base_url).hostname
    if not host:
        return []
    found: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = str(tag["href"]).strip()
        if not href or href.startswith("#") or href.lower().startswith("mailto:"):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.hostname and parsed.hostname.lower() == host.lower():
            found.append(absolute)
    return found


def extract_html_blocks(html: bytes, *, page_url: str) -> list[RawBlock]:
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in _DROP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    heading_path: list[str] = []
    blocks: list[RawBlock] = []

    def walk(node: Tag) -> None:
        nonlocal heading_path
        for child in list(node.children):
            if not isinstance(child, Tag):
                continue
            name = child.name.lower()
            if name in _DROP_TAGS:
                continue
            heading_match = _HEADING.match(name)
            if heading_match is not None:
                text = _visible_text(child)
                if not text:
                    continue
                title = text.split("\n", 1)[0].strip()
                level = int(heading_match.group(1))
                heading_path = heading_path[: level - 1] + [title]
                blocks.append(
                    RawBlock(
                        block_type="heading",
                        text=text,
                        locator=_locator(page_url, heading_path),
                        metadata={},
                    )
                )
                continue
            if name == "p":
                text = _visible_text(child)
                if text:
                    blocks.append(
                        RawBlock(
                            block_type="paragraph",
                            text=text,
                            locator=_locator(page_url, heading_path),
                            metadata={},
                        )
                    )
                continue
            if name in {"ul", "ol"}:
                for item in child.find_all("li", recursive=False):
                    text = _visible_text(item)
                    if text:
                        blocks.append(
                            RawBlock(
                                block_type="list_item",
                                text=text,
                                locator=_locator(page_url, heading_path),
                                metadata={},
                            )
                        )
                continue
            if name == "li":
                text = _visible_text(child)
                if text:
                    blocks.append(
                        RawBlock(
                            block_type="list_item",
                            text=text,
                            locator=_locator(page_url, heading_path),
                            metadata={},
                        )
                    )
                continue
            if name == "pre":
                text = child.get_text("\n", strip=False).strip("\n")
                if text.strip():
                    blocks.append(
                        RawBlock(
                            block_type="code",
                            text=text,
                            locator=_locator(page_url, heading_path),
                            metadata={},
                        )
                    )
                continue
            walk(child)

    body = soup.body or soup
    walk(body)
    return blocks