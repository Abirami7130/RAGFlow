"""Per-run vector sidecar (.npz) read, validate, and durable write."""

from __future__ import annotations

import io
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk, ProcessingRun, VectorIndex, VectorMapping
from app.modules.embeddings.batch import ChunkEmbeddingInput, embed_chunks_batch
from app.modules.embeddings.protocol import EmbeddingProvider
from app.modules.ingestion.storage import fsync_directory, fsync_file
from app.modules.vectorstore.paths import sidecar_abs_path, sidecar_rel_path


@dataclass(frozen=True, slots=True)
class SidecarPayload:
    ids: tuple[int, ...]
    vectors: tuple[tuple[float, ...], ...]
    dimension: int


def _l2_norm(vector: np.ndarray) -> float:
    return float(math.sqrt(np.sum(vector * vector, dtype=np.float64)))


def vectors_are_normalized(vectors: np.ndarray, *, atol: float = 1e-5) -> bool:
    if vectors.size == 0:
        return True
    for row in vectors:
        if row.size == 0:
            continue
        norm = _l2_norm(row)
        if norm == 0.0:
            if not np.allclose(row, 0.0, atol=atol):
                return False
            continue
        if not math.isclose(norm, 1.0, abs_tol=atol):
            return False
    return True


def validate_sidecar_arrays(
    *,
    ids: np.ndarray,
    vectors: np.ndarray,
    dimension: int,
    expected_ids: tuple[int, ...] | None = None,
) -> None:
    if ids.ndim != 1:
        raise ValueError("Sidecar ids must be a one-dimensional array.")
    if vectors.ndim != 2:
        raise ValueError("Sidecar vectors must be a two-dimensional array.")
    if ids.shape[0] != vectors.shape[0]:
        raise ValueError("Sidecar ids and vectors row count must match.")
    if vectors.shape[1] != dimension:
        raise ValueError("Sidecar vector dimension does not match the embedding model.")
    if ids.dtype not in (np.int64, np.int32):
        raise ValueError("Sidecar ids must use an integer dtype.")
    if vectors.dtype != np.float32:
        raise ValueError("Sidecar vectors must use float32.")
    if expected_ids is not None and tuple(int(value) for value in ids) != expected_ids:
        raise ValueError("Sidecar ids do not match the staged vector mappings.")
    if not vectors_are_normalized(vectors):
        raise ValueError("Sidecar vectors must be L2-normalized.")


def load_sidecar(path: Path) -> SidecarPayload:
    if not path.is_file():
        raise FileNotFoundError(path)
    with np.load(path, allow_pickle=False) as archive:
        if "ids" not in archive or "vectors" not in archive:
            raise ValueError("Sidecar must contain ids and vectors arrays.")
        ids = np.asarray(archive["ids"])
        vectors = np.asarray(archive["vectors"], dtype=np.float32)
    dimension = int(vectors.shape[1]) if vectors.ndim == 2 and vectors.size else 0
    validate_sidecar_arrays(ids=ids, vectors=vectors, dimension=dimension)
    id_tuple = tuple(int(value) for value in ids)
    vector_tuple = tuple(tuple(float(x) for x in row) for row in vectors)
    return SidecarPayload(ids=id_tuple, vectors=vector_tuple, dimension=dimension)


def sidecar_is_complete(
    path: Path,
    *,
    expected_ids: tuple[int, ...],
    dimension: int,
) -> bool:
    try:
        payload = load_sidecar(path)
    except (FileNotFoundError, ValueError, OSError):
        return False
    if payload.dimension != dimension:
        return False
    return payload.ids == expected_ids


def write_sidecar_atomic(path: Path, *, ids: np.ndarray, vectors: np.ndarray) -> None:
    validate_sidecar_arrays(ids=ids, vectors=vectors, dimension=int(vectors.shape[1]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    try:
        buffer = io.BytesIO()
        np.savez_compressed(
            buffer,
            ids=ids.astype(np.int64, copy=False),
            vectors=vectors.astype(np.float32, copy=False),
        )
        payload = buffer.getvalue()
        with tmp_path.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_file(tmp_path)
        os.replace(tmp_path, path)
        fsync_file(path)
        fsync_directory(path.parent)
    finally:
        tmp_path.unlink(missing_ok=True)


def staged_vector_ids_for_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    index_id: uuid.UUID,
) -> tuple[int, ...]:
    """Staged vector ids for a run's chunks in ``sequence`` order."""
    rows = _staged_rows_for_run(db, run_id=run_id, index_id=index_id)
    return tuple(mapping.vector_id for _, mapping in rows)


def _staged_rows_for_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    index_id: uuid.UUID,
) -> list[tuple[Chunk, VectorMapping]]:
    chunks = list(
        db.scalars(select(Chunk).where(Chunk.processing_run_id == run_id).order_by(Chunk.sequence)).all()
    )
    if not chunks:
        return []
    chunk_ids = [chunk.id for chunk in chunks]
    mappings = {
        row.chunk_id: row
        for row in db.scalars(
            select(VectorMapping).where(
                VectorMapping.index_id == index_id,
                VectorMapping.chunk_id.in_(chunk_ids),
                VectorMapping.state == "staged",
            )
        ).all()
    }
    rows: list[tuple[Chunk, VectorMapping]] = []
    for chunk in chunks:
        mapping = mappings.get(chunk.id)
        if mapping is None:
            raise ValueError(f"Chunk {chunk.id} has no staged vector mapping on index {index_id}.")
        rows.append((chunk, mapping))
    return rows


def persist_run_sidecar(
    db: Session,
    *,
    run_id: uuid.UUID,
    index_id: uuid.UUID,
    provider: EmbeddingProvider,
    force: bool = False,
) -> str:
    """Write the run sidecar from staged mappings and chunk text. Mappings stay staged."""
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise ValueError(f"Processing run {run_id} was not found.")
    index = db.get(VectorIndex, index_id)
    if index is None:
        raise ValueError(f"Vector index {index_id} was not found.")
    if index.knowledge_base_id != run.knowledge_base_id:
        raise ValueError("Vector index does not belong to the processing run knowledge base.")
    if provider.dimension != index.dimension:
        raise ValueError("Embedding provider dimension does not match the vector index.")

    rows = _staged_rows_for_run(db, run_id=run_id, index_id=index_id)
    rel = sidecar_rel_path(
        knowledge_base_id=run.knowledge_base_id,
        index_id=index_id,
        processing_run_id=run_id,
    )
    dest = sidecar_abs_path(
        knowledge_base_id=run.knowledge_base_id,
        index_id=index_id,
        processing_run_id=run_id,
    )
    expected_ids = tuple(mapping.vector_id for _, mapping in rows)

    if not force and rows and sidecar_is_complete(dest, expected_ids=expected_ids, dimension=index.dimension):
        return rel
    if not rows:
        ids = np.array([], dtype=np.int64)
        vectors = np.zeros((0, index.dimension), dtype=np.float32)
        write_sidecar_atomic(dest, ids=ids, vectors=vectors)
        return rel

    inputs = [ChunkEmbeddingInput(chunk_id=chunk.id, text=chunk.text) for chunk, _ in rows]
    results = embed_chunks_batch(inputs, provider=provider)
    embedded = [list(row.vector) for row in results]
    vectors = np.asarray(embedded, dtype=np.float32)
    ids = np.asarray(expected_ids, dtype=np.int64)
    validate_sidecar_arrays(ids=ids, vectors=vectors, dimension=index.dimension, expected_ids=expected_ids)
    write_sidecar_atomic(dest, ids=ids, vectors=vectors)

    for (_, mapping), vector in zip(rows, embedded, strict=True):
        mapping.l2_norm = _l2_norm(np.asarray(vector, dtype=np.float32))
    db.flush()
    return rel