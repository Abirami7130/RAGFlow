"""In-memory FAISS IndexIDMap2 over IndexFlatIP (inner product on normalized vectors)."""

from __future__ import annotations

from pathlib import Path

import faiss
import numpy as np

from app.modules.vectorstore.sidecar import SidecarPayload


class FaissIdMapIndex:
    """Minimal vector index adapter: prepared ids, L2-normalized vectors, exact IP search."""

    def __init__(self, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension
        self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))

    @property
    def size(self) -> int:
        return int(self._index.ntotal)

    def add(self, ids: list[int], vectors: list[list[float]]) -> None:
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must have the same length")
        if not ids:
            return
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] != self.dimension:
            raise ValueError(f"vectors must be shaped (n, {self.dimension})")
        id_array = np.asarray(ids, dtype=np.int64)
        self._index.add_with_ids(matrix, id_array)

    def search(self, vector: list[float], k: int) -> list[tuple[int, float]]:
        if k <= 0:
            return []
        query = np.asarray(vector, dtype=np.float32)
        if query.ndim != 1 or query.shape[0] != self.dimension:
            raise ValueError(f"query vector must have length {self.dimension}")
        if self._index.ntotal == 0:
            return []
        limit = min(k, self._index.ntotal)
        scores, labels = self._index.search(query.reshape(1, -1), limit)
        hits: list[tuple[int, float]] = []
        for label, score in zip(labels[0], scores[0], strict=True):
            if int(label) == -1:
                continue
            hits.append((int(label), float(score)))
        return hits

    def remove(self, ids: list[int]) -> None:
        if not ids:
            return
        selector = faiss.IDSelectorBatch(np.asarray(ids, dtype=np.int64))
        self._index.remove_ids(selector)

    def rebuild(self, items: list[tuple[int, list[float]]]) -> None:
        self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(self.dimension))
        if not items:
            return
        ids, vectors = zip(*items, strict=True)
        self.add(list(ids), list(vectors))

    def stored_vector_ids(self) -> tuple[int, ...]:
        mapped = faiss.vector_to_array(self._index.id_map)
        return tuple(sorted(int(value) for value in mapped))

    @classmethod
    def from_sidecar(cls, payload: SidecarPayload) -> FaissIdMapIndex:
        index = cls(payload.dimension)
        if not payload.ids:
            return index
        index.add(list(payload.ids), [list(row) for row in payload.vectors])
        return index

    @classmethod
    def read_from_file(cls, path: Path) -> FaissIdMapIndex:
        raw = faiss.read_index(str(path))
        if not isinstance(raw, faiss.IndexIDMap2):
            raise ValueError("FAISS file must be an IndexIDMap2 index.")
        wrapped = cls.__new__(cls)
        wrapped.dimension = int(raw.d)
        wrapped._index = raw
        return wrapped