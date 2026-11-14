"""Embedding provider contract (D5). Adapters implement this protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Local embedding model identity and batch encode APIs."""

    @property
    def model_id(self) -> str:
        """Hugging Face model id or stable fake id."""

    @property
    def revision(self) -> str:
        """Model revision recorded on processing runs and vector indexes."""

    @property
    def dimension(self) -> int:
        """Output vector length."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode passage texts in input order."""

    def embed_query(self, text: str) -> list[float]:
        """Encode a single query string."""