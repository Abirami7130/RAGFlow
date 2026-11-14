"""Phase 3M batch chunk embedding service."""

from __future__ import annotations

import math
import uuid

import pytest

from app.modules.embeddings import (
    ChunkEmbeddingInput,
    FakeEmbeddingProvider,
    embed_chunks_batch,
)


class _StubProvider:
    def __init__(self, *, rows: list[list[float]] | None = None) -> None:
        self._dimension = 4
        self._rows = rows

    @property
    def model_id(self) -> str:
        return "stub/model"

    @property
    def revision(self) -> str:
        return "rev"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._rows is not None:
            return self._rows
        return [[float(index), 0.0, 0.0, 0.0] for index in range(len(texts))]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]


class TestEmbedChunksBatch:
    def test_empty_batch(self) -> None:
        assert embed_chunks_batch([], provider=FakeEmbeddingProvider()) == ()

    def test_batch_preserves_chunk_order(self) -> None:
        provider = FakeEmbeddingProvider()
        chunks = [
            ChunkEmbeddingInput(chunk_id=uuid.UUID("00000000-0000-4000-8000-000000000001"), text="alpha"),
            ChunkEmbeddingInput(chunk_id=uuid.UUID("00000000-0000-4000-8000-000000000002"), text="beta"),
            ChunkEmbeddingInput(chunk_id=uuid.UUID("00000000-0000-4000-8000-000000000003"), text="gamma"),
        ]
        results = embed_chunks_batch(chunks, provider=provider)
        assert [row.chunk_id for row in results] == [chunk.chunk_id for chunk in chunks]
        for chunk, row in zip(chunks, results, strict=True):
            assert row.vector == tuple(provider.embed_documents([chunk.text])[0])

    def test_deterministic_with_fake_embedder(self) -> None:
        provider = FakeEmbeddingProvider()
        chunks = [
            ChunkEmbeddingInput(chunk_id=uuid.uuid4(), text="repeatable"),
            ChunkEmbeddingInput(chunk_id=uuid.uuid4(), text="second"),
        ]
        first = embed_chunks_batch(chunks, provider=provider)
        second = embed_chunks_batch(chunks, provider=provider)
        assert first == second

    def test_provider_count_mismatch_rejected(self) -> None:
        chunks = [
            ChunkEmbeddingInput(chunk_id=uuid.uuid4(), text="one"),
            ChunkEmbeddingInput(chunk_id=uuid.uuid4(), text="two"),
        ]
        provider = _StubProvider(rows=[[1.0, 0.0, 0.0, 0.0]])
        with pytest.raises(ValueError, match="returned 1 vectors for 2 chunk texts"):
            embed_chunks_batch(chunks, provider=provider)

    def test_inconsistent_dimensions_rejected(self) -> None:
        chunk_id = uuid.uuid4()
        chunks = [ChunkEmbeddingInput(chunk_id=chunk_id, text="sized")]
        provider = _StubProvider(rows=[[1.0, 2.0]])
        with pytest.raises(ValueError, match=f"chunk {chunk_id}.*dimension 2"):
            embed_chunks_batch(chunks, provider=provider)

    def test_non_finite_values_rejected(self) -> None:
        chunk_id = uuid.uuid4()
        chunks = [ChunkEmbeddingInput(chunk_id=chunk_id, text="bad")]
        for bad_value in (math.nan, math.inf, -math.inf):
            provider = _StubProvider(rows=[[1.0, bad_value, 0.0, 0.0]])
            with pytest.raises(ValueError, match="non-finite"):
                embed_chunks_batch(chunks, provider=provider)