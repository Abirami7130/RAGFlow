"""Worker and ingestion error types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class WorkerError(Exception):
    error_code: str
    message: str
    detail: str | None = None


class TransientWorkerError(WorkerError):
    """Eligible for automatic job retry (does not increment user_retry_count)."""


class PermanentWorkerError(WorkerError):
    """Terminal failure; job must not retry automatically."""


PERMANENT_ERROR_CODES = frozenset(
    {
        "unsupported_type",
        "file_too_large",
        "mime_mismatch",
        "pdf_unreadable",
        "docx_unreadable",
        "pptx_unreadable",
        "csv_unreadable",
        "xlsx_unreadable",
        "json_unreadable",
        "ssrf_blocked",
        "inconsistent_state",
        "extractor_not_implemented",
        "invalid_encoding",
        "nul_in_text",
        "empty_content",
        "inconsistent_blocks",
        "storage_missing",
        "document_deleted",
        "knowledge_base_deleted",
        "not_found",
        "redirect_limit",
        "activation_failed",
    }
)

TRANSIENT_ERROR_CODES = frozenset(
    {
        "worker_lost",
        "execution_lost",
        "storage_unavailable",
        "unexpected_error",
        "fetch_failed",
        "embed_failed",
        "vector_index_failed",
        "lexical_index_failed",
    }
)

AUTOMATIC_RETRY_DELAYS_SECONDS = (10, 30, 90)
# Initial claim plus three automatic retries (delays 10s, 30s, 90s); see DECISIONS D18.
INGEST_MAX_ATTEMPTS = 1 + len(AUTOMATIC_RETRY_DELAYS_SECONDS)
