"""Web URL ingestion helpers."""

from app.modules.ingestion.integrations.web.crawl import CrawlResult, crawl_and_extract

__all__ = ["CrawlResult", "crawl_and_extract"]