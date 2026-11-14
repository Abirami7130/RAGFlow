"""L2 normalization for embedding vectors."""

from __future__ import annotations

import math


def l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in values))
    if norm == 0.0:
        return values
    return [x / norm for x in values]