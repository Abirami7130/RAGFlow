"""Publish staged FAISS to the live generation and commit vector mappings."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Chunk, ProcessingRun, VectorIndex, VectorMapping
from app.modules.ingestion.storage import fsync_directory, fsync_file
from app.modules.vectorstore.faiss_index import FaissIdMapIndex
from app.modules.vectorstore.paths import (
    generation_stamp_abs_path,
    live_faiss_abs_path,
    sidecar_abs_path,
    staged_faiss_abs_path,
)
from app.modules.vectorstore.sidecar import SidecarPayload, load_sidecar, sidecar_is_complete, staged_vector_ids_for_run
from app.modules.vectorstore.staged_faiss import (
    staged_faiss_is_complete,
    validate_staged_faiss_file,
    write_faiss_atomic,
)


@dataclass(frozen=True, slots=True)
class PublishLiveFaissResult:
    index_id: uuid.UUID
    processing_run_id: uuid.UUID
    generation: int
    vector_count: int
    mappings_committed: int
    live_index_replaced: bool


def read_generation_stamp(path: Path) -> int | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return int(text)


def write_generation_stamp_atomic(path: Path, *, generation: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    try:
        tmp_path.write_text(f"{generation}\n", encoding="utf-8")
        fsync_file(tmp_path)
        os.replace(tmp_path, path)
        fsync_file(path)
        fsync_directory(path.parent)
    finally:
        tmp_path.unlink(missing_ok=True)


def _vector_from_sidecar(payload: SidecarPayload, vector_id: int) -> list[float]:
    try:
        row = payload.ids.index(vector_id)
    except ValueError as exc:
        raise ValueError(f"Vector id {vector_id} is missing from the sidecar.") from exc
    return list(payload.vectors[row])


def _load_sidecar_for_run(
    *,
    knowledge_base_id: uuid.UUID,
    index_id: uuid.UUID,
    processing_run_id: uuid.UUID,
) -> SidecarPayload:
    return load_sidecar(
        sidecar_abs_path(
            knowledge_base_id=knowledge_base_id,
            index_id=index_id,
            processing_run_id=processing_run_id,
        )
    )


def committed_vector_items(db: Session, *, index_id: uuid.UUID) -> list[tuple[int, list[float]]]:
    mappings = list(
        db.scalars(
            select(VectorMapping).where(VectorMapping.index_id == index_id, VectorMapping.state == "committed")
        ).all()
    )
    if not mappings:
        return []
    chunk_ids = [mapping.chunk_id for mapping in mappings]
    chunks = {
        chunk.id: chunk
        for chunk in db.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids))).all()
    }
    index = db.get(VectorIndex, index_id)
    if index is None:
        raise ValueError(f"Vector index {index_id} was not found.")
    payloads: dict[uuid.UUID, SidecarPayload] = {}
    items: list[tuple[int, list[float]]] = []
    for mapping in mappings:
        chunk = chunks.get(mapping.chunk_id)
        if chunk is None:
            raise ValueError(f"Chunk {mapping.chunk_id} was not found for a committed mapping.")
        run_id = chunk.processing_run_id
        if run_id not in payloads:
            payloads[run_id] = _load_sidecar_for_run(
                knowledge_base_id=index.knowledge_base_id,
                index_id=index_id,
                processing_run_id=run_id,
            )
        items.append((mapping.vector_id, _vector_from_sidecar(payloads[run_id], mapping.vector_id)))
    items.sort(key=lambda row: row[0])
    return items


def committed_vector_id_set(db: Session, *, index_id: uuid.UUID) -> set[int]:
    rows = db.scalars(
        select(VectorMapping.vector_id).where(VectorMapping.index_id == index_id, VectorMapping.state == "committed")
    ).all()
    return {int(value) for value in rows}


def live_index_vector_ids(path: Path) -> set[int]:
    if not path.is_file():
        return set()
    return set(FaissIdMapIndex.read_from_file(path).stored_vector_ids())


def rebuild_live_index_from_committed(db: Session, *, index_id: uuid.UUID) -> int:
    """Rewrite live index.faiss and stamp from committed mappings only. Returns DB generation."""
    index = db.get(VectorIndex, index_id)
    if index is None:
        raise ValueError(f"Vector index {index_id} was not found.")
    items = committed_vector_items(db, index_id=index_id)
    live_path = live_faiss_abs_path(knowledge_base_id=index.knowledge_base_id, index_id=index_id)
    stamp_path = generation_stamp_abs_path(knowledge_base_id=index.knowledge_base_id, index_id=index_id)
    live_index = FaissIdMapIndex(index.dimension)
    if items:
        live_index.add([row[0] for row in items], [row[1] for row in items])
    write_faiss_atomic(live_path, live_index)
    write_generation_stamp_atomic(stamp_path, generation=index.generation)
    return index.generation


def vector_index_needs_recovery(db: Session, *, index: VectorIndex) -> bool:
    live_path = live_faiss_abs_path(knowledge_base_id=index.knowledge_base_id, index_id=index.id)
    stamp_path = generation_stamp_abs_path(knowledge_base_id=index.knowledge_base_id, index_id=index.id)
    stamp = read_generation_stamp(stamp_path)
    committed_ids = committed_vector_id_set(db, index_id=index.id)
    if stamp != index.generation:
        return True
    if live_index_vector_ids(live_path) != committed_ids:
        return True
    if not committed_ids and not live_path.is_file():
        return stamp is not None
    return False


def recover_vector_index(db: Session, *, index_id: uuid.UUID) -> bool:
    index = db.get(VectorIndex, index_id)
    if index is None:
        raise ValueError(f"Vector index {index_id} was not found.")
    if not vector_index_needs_recovery(db, index=index):
        return False
    rebuild_live_index_from_committed(db, index_id=index_id)
    return True


def run_vector_index_recovery(db: Session) -> dict[str, int]:
    indexes = list(db.scalars(select(VectorIndex)).all())
    rebuilt = 0
    checked = 0
    for index in indexes:
        checked += 1
        if recover_vector_index(db, index_id=index.id):
            rebuilt += 1
    return {"indexes_checked": checked, "indexes_rebuilt": rebuilt}


def lookup_committed_mapping(
    db: Session,
    *,
    index_id: uuid.UUID,
    vector_id: int,
) -> VectorMapping | None:
    return db.scalar(
        select(VectorMapping).where(
            VectorMapping.index_id == index_id,
            VectorMapping.vector_id == vector_id,
            VectorMapping.state == "committed",
        )
    )


def filter_committed_search_hits(
    db: Session,
    *,
    index_id: uuid.UUID,
    hits: list[tuple[int, float]],
) -> list[tuple[int, float]]:
    if not hits:
        return []
    hit_ids = [vector_id for vector_id, _ in hits]
    committed = set(
        db.scalars(
            select(VectorMapping.vector_id).where(
                VectorMapping.index_id == index_id,
                VectorMapping.vector_id.in_(hit_ids),
                VectorMapping.state == "committed",
            )
        ).all()
    )
    return [(vector_id, score) for vector_id, score in hits if vector_id in committed]


def load_searchable_live_index(db: Session, *, index_id: uuid.UUID) -> tuple[FaissIdMapIndex, VectorIndex]:
    recover_vector_index(db, index_id=index_id)
    index = db.get(VectorIndex, index_id)
    if index is None:
        raise ValueError(f"Vector index {index_id} was not found.")
    live_path = live_faiss_abs_path(knowledge_base_id=index.knowledge_base_id, index_id=index_id)
    stamp_path = generation_stamp_abs_path(knowledge_base_id=index.knowledge_base_id, index_id=index_id)
    if read_generation_stamp(stamp_path) != index.generation:
        raise ValueError("Live generation stamp does not match the database generation.")
    if not live_path.is_file():
        return FaissIdMapIndex(index.dimension), index
    return FaissIdMapIndex.read_from_file(live_path), index


def publish_live_faiss_for_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    index_id: uuid.UUID,
    commit_mappings: bool = True,
) -> PublishLiveFaissResult:
    """Promote staged artifacts, then commit mappings when the live index and sidecar are valid."""
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise ValueError(f"Processing run {run_id} was not found.")
    index = db.get(VectorIndex, index_id)
    if index is None:
        raise ValueError(f"Vector index {index_id} was not found.")
    if index.knowledge_base_id != run.knowledge_base_id:
        raise ValueError("Vector index does not belong to the processing run knowledge base.")

    staged_ids = staged_vector_ids_for_run(db, run_id=run_id, index_id=index_id)
    sidecar_path = sidecar_abs_path(
        knowledge_base_id=run.knowledge_base_id,
        index_id=index_id,
        processing_run_id=run_id,
    )
    staged_path = staged_faiss_abs_path(
        knowledge_base_id=run.knowledge_base_id,
        index_id=index_id,
        processing_run_id=run_id,
    )
    if not sidecar_is_complete(sidecar_path, expected_ids=staged_ids, dimension=index.dimension):
        raise ValueError("Vector sidecar is missing or does not match staged mappings.")
    if not staged_faiss_is_complete(staged_path, dimension=index.dimension, expected_ids=staged_ids):
        raise ValueError("Staged FAISS artifact is missing or invalid.")
    validate_staged_faiss_file(staged_path, dimension=index.dimension, expected_ids=staged_ids)
    run_sidecar = load_sidecar(sidecar_path)

    merged: dict[int, list[float]] = {vector_id: vector for vector_id, vector in committed_vector_items(db, index_id=index_id)}
    for vector_id in staged_ids:
        merged[vector_id] = _vector_from_sidecar(run_sidecar, vector_id)
    items = sorted(merged.items(), key=lambda row: row[0])

    live_path = live_faiss_abs_path(knowledge_base_id=index.knowledge_base_id, index_id=index_id)
    stamp_path = generation_stamp_abs_path(knowledge_base_id=index.knowledge_base_id, index_id=index_id)
    live_was_complete = not vector_index_needs_recovery(db, index=index)

    live_index = FaissIdMapIndex(index.dimension)
    if items:
        live_index.add([row[0] for row in items], [row[1] for row in items])
    write_faiss_atomic(live_path, live_index)

    new_generation = index.generation + 1
    write_generation_stamp_atomic(stamp_path, generation=new_generation)

    if not sidecar_is_complete(sidecar_path, expected_ids=staged_ids, dimension=index.dimension):
        raise ValueError("Vector sidecar became invalid before mapping commit.")
    if live_index.stored_vector_ids() != tuple(row[0] for row in items):
        raise ValueError("Live FAISS ids do not match the publish set.")

    mappings_committed = 0
    if commit_mappings:
        chunk_ids = list(
            db.scalars(select(Chunk.id).where(Chunk.processing_run_id == run_id)).all()
        )
        if chunk_ids:
            mappings_committed = db.execute(
                update(VectorMapping)
                .where(
                    VectorMapping.index_id == index_id,
                    VectorMapping.chunk_id.in_(chunk_ids),
                    VectorMapping.state == "staged",
                )
                .values(state="committed")
            ).rowcount or 0
        index.generation = new_generation
        index.vector_count = len(items)
        db.flush()
        if mappings_committed != len(staged_ids):
            raise ValueError("Not every staged mapping for the run was committed.")
    else:
        new_generation = index.generation

    return PublishLiveFaissResult(
        index_id=index_id,
        processing_run_id=run_id,
        generation=new_generation,
        vector_count=len(items),
        mappings_committed=mappings_committed,
        live_index_replaced=not live_was_complete or bool(items),
    )