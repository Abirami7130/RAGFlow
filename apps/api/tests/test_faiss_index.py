"""Phase 3G in-memory FAISS IndexIDMap2 adapter."""

from __future__ import annotations

import math

import pytest

from app.modules.vectorstore.faiss_index import FaissIdMapIndex

faiss = pytest.importorskip("faiss")


def _unit(*components: float) -> list[float]:
    norm = math.sqrt(sum(value * value for value in components))
    return [value / norm for value in components]


class TestFaissIdMapIndex:
    def test_id_round_trip(self) -> None:
        index = FaissIdMapIndex(3)
        vectors = [_unit(1, 0, 0), _unit(0, 1, 0)]
        index.add([42, 7], vectors)
        hits = index.search(_unit(1, 0, 0), k=2)
        assert [vector_id for vector_id, _ in hits] == [42, 7]
        assert hits[0][1] == pytest.approx(1.0, abs=1e-5)

    def test_stable_ranking_with_fixed_vectors(self) -> None:
        index = FaissIdMapIndex(3)
        basis_x = _unit(1, 0, 0)
        basis_y = _unit(0, 1, 0)
        diagonal = _unit(1, 1, 0)
        index.add([1, 2, 3], [basis_x, basis_y, diagonal])
        query = basis_x
        first = index.search(query, k=3)
        second = index.search(query, k=3)
        assert first == second
        assert [vector_id for vector_id, _ in first] == [1, 3, 2]
        assert first[0][1] == pytest.approx(1.0, abs=1e-5)
        assert first[1][1] == pytest.approx(math.sqrt(0.5), abs=1e-5)
        assert first[2][1] == pytest.approx(0.0, abs=1e-5)

    def test_dimension_validation(self) -> None:
        with pytest.raises(ValueError, match="dimension must be positive"):
            FaissIdMapIndex(0)
        index = FaissIdMapIndex(2)
        with pytest.raises(ValueError, match="vectors must be shaped"):
            index.add([1], [[1.0, 0.0, 0.1]])
        with pytest.raises(ValueError, match="query vector must have length"):
            index.search([1.0], k=1)

    def test_empty_input(self) -> None:
        index = FaissIdMapIndex(4)
        assert index.search(_unit(1, 0, 0, 0), k=5) == []
        index.add([], [])
        assert index.size == 0
        index.add([1], [_unit(1, 0, 0, 0)])
        assert index.search(_unit(0, 1, 0, 0), k=0) == []