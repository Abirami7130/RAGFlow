"""Approved ingestion pipeline stage order."""

INGESTION_STAGES: tuple[str, ...] = (
    "extract",
    "normalize",
    "chunk",
    "embed",
    "vector_index",
    "lexical_index",
    "activate",
)
