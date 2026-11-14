"""Bounded same-host crawl and HTML extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

import httpx

from app.domain.extraction import ExtractionError, RawBlock
from app.modules.ingestion.integrations.web import fetch as web_fetch
from app.modules.ingestion.integrations.web.html import discover_links, extract_html_blocks
from app.modules.ingestion.url_validation import canonicalize_url

ResolveFn = Callable[[str], tuple[list[str], list[str]]]


@dataclass(slots=True)
class CrawlResult:
    blocks: list[RawBlock]
    trace_output: dict[str, object] = field(default_factory=dict)


def crawl_and_extract(
    *,
    seed_url: str,
    max_pages: int = 20,
    depth: int = 1,
    resolve: ResolveFn | None = None,
    transport: httpx.BaseTransport | None = None,
) -> CrawlResult:
    max_pages = min(max(1, max_pages), 20)
    depth = min(max(0, depth), 1)
    seed = canonicalize_url(seed_url)
    seed_host = (urlparse(seed).hostname or "").lower()

    queue: list[tuple[str, int]] = [(seed, 0)]
    seen: set[str] = {seed}
    blocks: list[RawBlock] = []
    pages: list[dict[str, object]] = []
    skipped_urls: list[dict[str, str]] = []
    cookies: dict[str, str] = {}

    while queue and len(pages) < max_pages:
        url, page_depth = queue.pop(0)
        try:
            response = web_fetch.fetch_url(
                url,
                resolve=resolve,
                transport=transport,
                cookies=cookies if urlparse(url).hostname == urlparse(seed).hostname else {},
            )
        except ExtractionError as exc:
            if url == seed and not pages:
                raise
            skipped_urls.append({"url": url, "reason": exc.code})
            continue

        page_blocks = extract_html_blocks(response.body, page_url=response.final_url)
        pages.append(
            {
                "url": url,
                "final_url": response.final_url,
                "status": response.status_code,
                "error_code": None,
                "block_count": len(page_blocks),
            }
        )
        blocks.extend(page_blocks)

        if page_depth >= depth:
            continue
        for link in discover_links(response.body, base_url=response.final_url):
            canonical = canonicalize_url(link)
            parsed = urlparse(canonical)
            if (parsed.hostname or "").lower() != seed_host:
                skipped_urls.append({"url": canonical, "reason": "off_host"})
                continue
            if canonical in seen:
                continue
            if len(seen) >= max_pages:
                break
            seen.add(canonical)
            queue.append((canonical, page_depth + 1))

    if not pages:
        raise ExtractionError("fetch_failed", "The seed URL could not be fetched.")

    return CrawlResult(
        blocks=blocks,
        trace_output={"pages": pages, "skipped_urls": skipped_urls},
    )