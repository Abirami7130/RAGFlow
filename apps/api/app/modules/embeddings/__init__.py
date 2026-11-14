"""Embedding provider protocol and test doubles."""

from app.modules.embeddings.batch import ChunkEmbeddingInput, ChunkEmbeddingResult, embed_chunks_batch
from app.modules.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingError,
    EmbeddingModelLoadError,
)
from app.modules.embeddings.factory import embedding_provider_from_settings
from app.modules.embeddings.fake import (
    FAKE_EMBEDDING_DIMENSION,
    FAKE_EMBEDDING_MODEL_ID,
    FAKE_EMBEDDING_REVISION,
    FakeEmbeddingProvider,
)
from app.modules.embeddings.protocol import EmbeddingProvider
from app.modules.embeddings.sentence_transformers_provider import (
    DEFAULT_BGE_DIMENSION,
    DEFAULT_BGE_MODEL_ID,
    SentenceTransformersEmbeddingProvider,
    revision_for_model,
)

__all__ = [
    "ChunkEmbeddingInput",
    "ChunkEmbeddingResult",
    "DEFAULT_BGE_DIMENSION",
    "DEFAULT_BGE_MODEL_ID",
    "EmbeddingConfigurationError",
    "EmbeddingError",
    "EmbeddingModelLoadError",
    "EmbeddingProvider",
    "FAKE_EMBEDDING_DIMENSION",
    "FAKE_EMBEDDING_MODEL_ID",
    "FAKE_EMBEDDING_REVISION",
    "FakeEmbeddingProvider",
    "SentenceTransformersEmbeddingProvider",
    "embed_chunks_batch",
    "embedding_provider_from_settings",
    "revision_for_model",
]