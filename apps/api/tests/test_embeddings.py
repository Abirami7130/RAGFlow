"""Phase 3B embedding provider contract and fake embedder."""

from __future__ import annotations

import math

import pytest

from app.modules.embeddings import (
    FAKE_EMBEDDING_DIMENSION,
    FAKE_EMBEDDING_MODEL_ID,
    FAKE_EMBEDDING_REVISION,
    EmbeddingProvider,
    FakeEmbeddingProvider,
)


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


class TestFakeEmbeddingProvider:
    def test_implements_protocol(self) -> None:
        provider = FakeEmbeddingProvider()
        assert isinstance(provider, EmbeddingProvider)

    def test_model_metadata(self) -> None:
        provider = FakeEmbeddingProvider()
        assert provider.model_id == FAKE_EMBEDDING_MODEL_ID
        assert provider.revision == FAKE_EMBEDDING_REVISION
        assert provider.dimension == FAKE_EMBEDDING_DIMENSION

    def test_deterministic_embeddings(self) -> None:
        provider = FakeEmbeddingProvider()
        text = "chunk about retrieval"
        first = provider.embed_query(text)
        second = provider.embed_query(text)
        assert first == second
        doc_once = provider.embed_documents([text])[0]
        doc_twice = provider.embed_documents([text, text])
        assert doc_once == doc_twice[0] == doc_twice[1]

    def test_query_and_passage_differ_for_same_text(self) -> None:
        provider = FakeEmbeddingProvider()
        text = "shared wording"
        assert provider.embed_query(text) != provider.embed_documents([text])[0]

    def test_vector_dimension(self) -> None:
        dim = 32
        provider = FakeEmbeddingProvider(dimension=dim)
        vec = provider.embed_query("sized")
        assert len(vec) == dim
        batch = provider.embed_documents(["a", "bb"])
        assert all(len(row) == dim for row in batch)

    def test_non_empty_vectors_are_unit_length(self) -> None:
        provider = FakeEmbeddingProvider()
        for text in ("token", "another phrase"):
            for vec in (provider.embed_query(text), provider.embed_documents([text])[0]):
                assert len(vec) == provider.dimension
                assert _norm(vec) == pytest.approx(1.0, abs=1e-6)

    def test_empty_input_returns_zero_vector(self) -> None:
        provider = FakeEmbeddingProvider()
        empty_query = provider.embed_query("")
        empty_doc = provider.embed_documents([""])[0]
        expected = [0.0] * provider.dimension
        assert empty_query == expected
        assert empty_doc == expected

    def test_batch_preserves_order(self) -> None:
        provider = FakeEmbeddingProvider()
        texts = ["alpha", "beta", "gamma"]
        batch = provider.embed_documents(texts)
        assert len(batch) == len(texts)
        assert batch[0] != batch[1] != batch[2]
        for index, text in enumerate(texts):
            assert batch[index] == provider.embed_documents([text])[0]

    def test_empty_batch(self) -> None:
        provider = FakeEmbeddingProvider()
        assert provider.embed_documents([]) == []

    def test_invalid_dimension_rejected(self) -> None:
        with pytest.raises(ValueError, match="dimension must be positive"):
            FakeEmbeddingProvider(dimension=0)