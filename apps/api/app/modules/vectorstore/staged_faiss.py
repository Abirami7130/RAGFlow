"""Durable per-run staged FAISS index files (not the live index.faiss)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import faiss

from app.modules.ingestion.storage import fsync_directory, fsync_file
from app.modules.vectorstore.faiss_index import FaissIdMapIndex
from app.modules.vectorstore.paths import sidecar_abs_path, staged_faiss_abs_path, staged_faiss_rel_path
from app.modules.vectorstore.sidecar import SidecarPayload, load_sidecar


def write_faiss_atomic(path: Path, index: FaissIdMapIndex) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    try:
        faiss.write_index(index._index, str(tmp_path))
        fsync_file(tmp_path)
        os.replace(tmp_path, path)
        fsync_file(path)
        fsync_directory(path.parent)
    finally:
        tmp_path.unlink(missing_ok=True)


def validate_staged_faiss_file(
    path: Path,
    *,
    dimension: int,
    expected_ids: tuple[int, ...],
) -> FaissIdMapIndex:
    if not path.is_file():
        raise FileNotFoundError(path)
    index = FaissIdMapIndex.read_from_file(path)
    if index.dimension != dimension:
        raise ValueError("Staged FAISS dimension does not match.")
    if index.size != len(expected_ids):
        raise ValueError("Staged FAISS vector count does not match expected ids.")
    if index.stored_vector_ids() != tuple(sorted(expected_ids)):
        raise ValueError("Staged FAISS ids do not match expected ids.")
    return index


def staged_faiss_is_complete(
    path: Path,
    *,
    dimension: int,
    expected_ids: tuple[int, ...],
) -> bool:
    try:
        validate_staged_faiss_file(path, dimension=dimension, expected_ids=expected_ids)
    except (FileNotFoundError, ValueError, OSError, RuntimeError):
        return False
    return True


def build_staged_faiss_from_sidecar(
    sidecar_path: Path,
    *,
    staged_path: Path,
    force: bool = False,
) -> FaissIdMapIndex:
    payload = load_sidecar(sidecar_path)
    expected_ids = payload.ids
    if not force and staged_faiss_is_complete(
        staged_path,
        dimension=payload.dimension,
        expected_ids=expected_ids,
    ):
        return FaissIdMapIndex.read_from_file(staged_path)

    index = FaissIdMapIndex.from_sidecar(payload)
    write_faiss_atomic(staged_path, index)
    return validate_staged_faiss_file(
        staged_path,
        dimension=payload.dimension,
        expected_ids=expected_ids,
    )


def persist_staged_faiss_for_run(
    *,
    knowledge_base_id: uuid.UUID,
    index_id: uuid.UUID,
    processing_run_id: uuid.UUID,
    force: bool = False,
) -> str:
    sidecar_path = sidecar_abs_path(
        knowledge_base_id=knowledge_base_id,
        index_id=index_id,
        processing_run_id=processing_run_id,
    )
    staged_path = staged_faiss_abs_path(
        knowledge_base_id=knowledge_base_id,
        index_id=index_id,
        processing_run_id=processing_run_id,
    )
    build_staged_faiss_from_sidecar(sidecar_path, staged_path=staged_path, force=force)
    return staged_faiss_rel_path(
        knowledge_base_id=knowledge_base_id,
        index_id=index_id,
        processing_run_id=processing_run_id,
    )


def round_trip_sidecar_search(
    staged_path: Path,
    payload: SidecarPayload,
) -> None:
    """Validate each sidecar row is retrievable from the staged FAISS index."""
    index = validate_staged_faiss_file(
        staged_path,
        dimension=payload.dimension,
        expected_ids=payload.ids,
    )
    for vector_id, vector in zip(payload.ids, payload.vectors, strict=True):
        hits = index.search(list(vector), k=1)
        if not hits or hits[0][0] != vector_id:
            raise ValueError(f"Staged FAISS search did not return vector id {vector_id}.")