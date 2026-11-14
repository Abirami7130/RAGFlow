"""Deterministic fake embedder for unit tests (no model download)."""

from __future__ import annotations

import hashlib
import struct

from app.modules.embeddings.normalize import l2_normalize

FAKE_EMBEDDING_MODEL_ID = "ragflow/fake-embedder"
FAKE_EMBEDDING_REVISION = "deterministic-v1"
FAKE_EMBEDDING_DIMENSION = 384


def _vector_from_text(text: str, *, dimension: int, role: bytes) -> list[float]:
    if text == "":
        return [0.0] * dimension
    seed = hashlib.sha256(role + b"\0" + text.encode("utf-8")).digest()
    values: list[float] = []
    block_index = 0
    while len(values) < dimension:
        block = hashlib.sha256(seed + block_index.to_bytes(4, "big")).digest()
        for offset in range(0, len(block) - 3, 4):
            packed = struct.unpack("!I", block[offset : offset + 4])[0]
            values.append((packed / 2**32) * 2.0 - 1.0)
            if len(values) >= dimension:
                break
        block_index += 1
    return l2_normalize(values)


class FakeEmbeddingProvider:
    """Repeatable fixed-dimension vectors derived from input text."""

    def __init__(
        self,
        *,
        dimension: int = FAKE_EMBEDDING_DIMENSION,
        model_id: str = FAKE_EMBEDDING_MODEL_ID,
        revision: str = FAKE_EMBEDDING_REVISION,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dimension = dimension
        self._model_id = model_id
        self._revision = revision

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_vector_from_text(text, dimension=self._dimension, role=b"passage") for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return _vector_from_text(text, dimension=self._dimension, role=b"query")