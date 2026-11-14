"""Batch chunk embedding over an :class:`EmbeddingProvider` (no persistence)."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from app.modules.embeddings.protocol import EmbeddingProvider


@dataclass(frozen=True, slots=True)
class ChunkEmbeddingInput:
    chunk_id: uuid.UUID
    text: str


@dataclass(frozen=True, slots=True)
class ChunkEmbeddingResult:
    chunk_id: uuid.UUID
    vector: tuple[float, ...]


def _validate_vectors(
    *,
    chunks: list[ChunkEmbeddingInput],
    vectors: list[list[float]],
    expected_dimension: int,
) -> None:
    if len(vectors) != len(chunks):
        raise ValueError(
            f"Embedding provider returned {len(vectors)} vectors for {len(chunks)} chunk texts."
        )
    for index, (chunk, row) in enumerate(zip(chunks, vectors, strict=True)):
        if len(row) != expected_dimension:
            raise ValueError(
                f"Embedding vector for chunk {chunk.chunk_id} has dimension {len(row)}; "
                f"expected {expected_dimension}."
            )
        for value_index, value in enumerate(row):
            if not math.isfinite(value):
                raise ValueError(
                    f"Embedding vector for chunk {chunk.chunk_id} has non-finite value "
                    f"at index {value_index}."
                )


def embed_chunks_batch(
    chunks: list[ChunkEmbeddingInput],
    *,
    provider: EmbeddingProvider,
) -> tuple[ChunkEmbeddingResult, ...]:
    """Embed chunk texts in input order and return matching ``(chunk_id, vector)`` pairs."""
    if not chunks:
        return ()
    texts = [item.text for item in chunks]
    embedded = provider.embed_documents(texts)
    _validate_vectors(chunks=chunks, vectors=embedded, expected_dimension=provider.dimension)
    return tuple(
        ChunkEmbeddingResult(chunk_id=item.chunk_id, vector=tuple(row))
        for item, row in zip(chunks, embedded, strict=True)
    )