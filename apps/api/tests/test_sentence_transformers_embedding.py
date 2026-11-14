"""Phase 3S Sentence Transformers embedding provider and configuration."""

from __future__ import annotations

import math
import os
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.core.config import Settings
from app.modules.embeddings import (
    DEFAULT_BGE_DIMENSION,
    DEFAULT_BGE_MODEL_ID,
    EmbeddingConfigurationError,
    EmbeddingModelLoadError,
    EmbeddingProvider,
    FakeEmbeddingProvider,
    SentenceTransformersEmbeddingProvider,
    embedding_provider_from_settings,
    revision_for_model,
)


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


class TestEmbeddingConfiguration:
    def test_default_bge_constants(self) -> None:
        assert DEFAULT_BGE_MODEL_ID == "BAAI/bge-small-en-v1.5"
        assert DEFAULT_BGE_DIMENSION == 384

    def test_factory_returns_fake_when_configured(self) -> None:
        settings = Settings(
            jwt_secret="x" * 32,
            embedding_use_fake=True,
        )
        provider = embedding_provider_from_settings(settings)
        assert isinstance(provider, FakeEmbeddingProvider)

    def test_factory_rejects_empty_model_id(self) -> None:
        settings = Settings(jwt_secret="x" * 32, embedding_model="  ", embedding_use_fake=False)
        with pytest.raises(EmbeddingConfigurationError, match="EMBEDDING_MODEL"):
            embedding_provider_from_settings(settings)

    def test_factory_builds_sentence_transformers_provider(self) -> None:
        settings = Settings(
            jwt_secret="x" * 32,
            embedding_model=DEFAULT_BGE_MODEL_ID,
            embedding_use_fake=False,
        )
        provider = embedding_provider_from_settings(settings)
        assert isinstance(provider, SentenceTransformersEmbeddingProvider)
        assert provider.model_id == DEFAULT_BGE_MODEL_ID
        assert provider.dimension == DEFAULT_BGE_DIMENSION

    def test_revision_for_model_format(self) -> None:
        assert revision_for_model("BAAI/bge-small-en-v1.5", library_version="3.0.1") == "3.0.1+BAAI/bge-small-en-v1.5"


class TestSentenceTransformersEmbeddingProvider:
    def test_metadata_before_load(self) -> None:
        provider = SentenceTransformersEmbeddingProvider(
            model_id=DEFAULT_BGE_MODEL_ID,
            revision="pinned-rev",
        )
        assert isinstance(provider, EmbeddingProvider)
        assert provider.model_id == DEFAULT_BGE_MODEL_ID
        assert provider.revision == "pinned-rev"
        assert provider.dimension == DEFAULT_BGE_DIMENSION

    def test_rejects_empty_model_id(self) -> None:
        with pytest.raises(EmbeddingConfigurationError, match="empty"):
            SentenceTransformersEmbeddingProvider(model_id="  ")

    def test_rejects_invalid_dimension(self) -> None:
        with pytest.raises(EmbeddingConfigurationError, match="dimension"):
            SentenceTransformersEmbeddingProvider(dimension=0)

    def test_missing_library_raises_configuration_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("simulated missing package")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        provider = SentenceTransformersEmbeddingProvider()
        with pytest.raises(EmbeddingConfigurationError, match="sentence-transformers is not installed"):
            provider.embed_query("hello")

    def test_encode_normalizes_and_handles_empty(self) -> None:
        vectors = np.array([[3.0, 4.0] + [0.0] * 382, [1.0, 0.0] + [0.0] * 382], dtype=np.float32)

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = DEFAULT_BGE_DIMENSION
        mock_model.encode.return_value = vectors

        provider = SentenceTransformersEmbeddingProvider(dimension=DEFAULT_BGE_DIMENSION)
        provider._model = mock_model
        provider._resolved_revision = "9.9.9+BAAI/bge-small-en-v1.5"

        doc_vecs = provider.embed_documents(["text", ""])
        assert len(doc_vecs) == 2
        assert doc_vecs[1] == [0.0] * DEFAULT_BGE_DIMENSION
        assert _norm(doc_vecs[0]) == pytest.approx(1.0, abs=1e-6)

        query_vec = provider.embed_query("question")
        assert len(query_vec) == DEFAULT_BGE_DIMENSION
        assert _norm(query_vec) == pytest.approx(1.0, abs=1e-6)
        mock_model.encode.assert_called()

    def test_load_failure_wraps_model_load_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys
        import types

        def explode(_model_id: str):
            raise OSError("weights missing")

        fake = types.ModuleType("sentence_transformers")
        fake.__version__ = "3.0.0"
        fake.SentenceTransformer = explode
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)

        provider = SentenceTransformersEmbeddingProvider()
        with pytest.raises(EmbeddingModelLoadError, match="Failed to load"):
            provider.embed_documents(["a"])

    def test_dimension_mismatch_on_load_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys
        import types

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 512

        fake = types.ModuleType("sentence_transformers")
        fake.__version__ = "3.0.0"
        fake.SentenceTransformer = lambda _model_id: mock_model
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)

        provider = SentenceTransformersEmbeddingProvider()
        with pytest.raises(EmbeddingConfigurationError, match="reports dimension"):
            provider.embed_query("x")


@pytest.mark.integration
def test_live_bge_small_en_v1_5_round_trip() -> None:
    if not os.environ.get("RUN_EMBEDDING_INTEGRATION"):
        pytest.skip("Set RUN_EMBEDDING_INTEGRATION=1 to download and run the production embedding model.")
    pytest.importorskip("sentence_transformers")
    provider = SentenceTransformersEmbeddingProvider()
    assert provider.model_id == DEFAULT_BGE_MODEL_ID
    assert provider.dimension == DEFAULT_BGE_DIMENSION
    vec = provider.embed_query("integration probe")
    assert len(vec) == DEFAULT_BGE_DIMENSION
    assert _norm(vec) == pytest.approx(1.0, abs=1e-4)