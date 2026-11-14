"""Production embedding provider using sentence-transformers (D6)."""

from __future__ import annotations

from threading import Lock
from typing import Any

from app.modules.embeddings.errors import EmbeddingConfigurationError, EmbeddingModelLoadError
from app.modules.embeddings.normalize import l2_normalize

DEFAULT_BGE_MODEL_ID = "BAAI/bge-small-en-v1.5"
DEFAULT_BGE_DIMENSION = 384


def revision_for_model(model_id: str, *, library_version: str) -> str:
    """Stable revision string persisted on runs and vector indexes (D6)."""
    return f"{library_version}+{model_id}"


class SentenceTransformersEmbeddingProvider:
    """Lazy-loaded Sentence Transformers adapter behind :class:`EmbeddingProvider`."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_BGE_MODEL_ID,
        revision: str | None = None,
        dimension: int = DEFAULT_BGE_DIMENSION,
    ) -> None:
        trimmed = model_id.strip()
        if not trimmed:
            raise EmbeddingConfigurationError("embedding model id must not be empty.")
        if dimension <= 0:
            raise EmbeddingConfigurationError("embedding dimension must be positive.")
        self._model_id = trimmed
        self._dimension = dimension
        self._revision_override = revision.strip() if revision else None
        self._resolved_revision: str | None = self._revision_override
        self._model: Any = None
        self._lock = Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def revision(self) -> str:
        if self._resolved_revision is None:
            self._ensure_model()
        assert self._resolved_revision is not None
        return self._resolved_revision

    @property
    def dimension(self) -> int:
        return self._dimension

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingConfigurationError(
                    "sentence-transformers is not installed. "
                    'Install with: pip install -e ".[embeddings]"'
                ) from exc
            try:
                import sentence_transformers

                library_version = sentence_transformers.__version__
                model = SentenceTransformer(self._model_id)
            except Exception as exc:
                raise EmbeddingModelLoadError(
                    f"Failed to load embedding model {self._model_id!r}.",
                    model_id=self._model_id,
                    cause=exc,
                ) from exc

            reported = int(model.get_sentence_embedding_dimension())
            if reported != self._dimension:
                raise EmbeddingConfigurationError(
                    f"Model {self._model_id!r} reports dimension {reported}, "
                    f"expected {self._dimension}."
                )
            if self._revision_override is None:
                self._resolved_revision = revision_for_model(self._model_id, library_version=library_version)
            self._model = model

    def _encode(self, texts: list[str], *, query: bool) -> list[list[float]]:
        self._ensure_model()
        assert self._model is not None
        if not texts:
            return []
        try:
            import numpy as np

            raw = self._model.encode(
                texts,
                normalize_embeddings=False,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            rows = np.atleast_2d(raw)
        except Exception as exc:
            raise EmbeddingModelLoadError(
                f"Embedding encode failed for model {self._model_id!r}.",
                model_id=self._model_id,
                cause=exc,
            ) from exc

        out: list[list[float]] = []
        for index, text in enumerate(texts):
            if text == "":
                out.append([0.0] * self._dimension)
                continue
            vec = [float(x) for x in rows[index]]
            if len(vec) != self._dimension:
                raise EmbeddingModelLoadError(
                    f"Encode returned dimension {len(vec)}, expected {self._dimension}.",
                    model_id=self._model_id,
                )
            out.append(l2_normalize(vec))
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, query=False)

    def embed_query(self, text: str) -> list[float]:
        rows = self._encode([text], query=True)
        return rows[0] if rows else [0.0] * self._dimension