"""Resolve the configured embedding provider without eager model download."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.modules.embeddings.errors import EmbeddingConfigurationError
from app.modules.embeddings.fake import FakeEmbeddingProvider
from app.modules.embeddings.protocol import EmbeddingProvider
from app.modules.embeddings.sentence_transformers_provider import (
    DEFAULT_BGE_DIMENSION,
    SentenceTransformersEmbeddingProvider,
)


def embedding_provider_from_settings(settings: Settings | None = None) -> EmbeddingProvider:
    """Return the fake embedder when `embedding_use_fake` is set; otherwise Sentence Transformers."""
    resolved = settings or get_settings()
    if resolved.embedding_use_fake:
        return FakeEmbeddingProvider()
    model_id = resolved.embedding_model.strip()
    if not model_id:
        raise EmbeddingConfigurationError("EMBEDDING_MODEL must not be empty when not using the fake embedder.")
    return SentenceTransformersEmbeddingProvider(
        model_id=model_id,
        revision=resolved.embedding_revision or None,
        dimension=DEFAULT_BGE_DIMENSION,
    )