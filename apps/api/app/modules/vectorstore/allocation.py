"""Create vector indexes and allocate staged vector ids for a processing run."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Chunk, ProcessingRun, VectorIndex, VectorMapping


@dataclass(frozen=True, slots=True)
class StagedVectorMappings:
    index_id: uuid.UUID
    """Chunk ids and vector ids in chunk `sequence` order."""

    pairs: tuple[tuple[uuid.UUID, int], ...]


def _index_identity_filter(
    *,
    knowledge_base_id: uuid.UUID,
    embedding_model: str,
    embedding_revision: str,
    dimension: int,
    metric: str,
):
    return (
        VectorIndex.knowledge_base_id == knowledge_base_id,
        VectorIndex.embedding_model == embedding_model,
        VectorIndex.embedding_revision == embedding_revision,
        VectorIndex.dimension == dimension,
        VectorIndex.metric == metric,
    )


def get_or_create_vector_index(
    db: Session,
    *,
    knowledge_base_id: uuid.UUID,
    embedding_model: str,
    embedding_revision: str,
    dimension: int,
    metric: str = "ip",
) -> VectorIndex:
    """Return the KB index row for this embedding identity, creating it when missing."""
    filters = _index_identity_filter(
        knowledge_base_id=knowledge_base_id,
        embedding_model=embedding_model,
        embedding_revision=embedding_revision,
        dimension=dimension,
        metric=metric,
    )
    index = db.scalars(select(VectorIndex).where(*filters)).first()
    if index is not None:
        return index

    try:
        with db.begin_nested():
            index = VectorIndex(
                knowledge_base_id=knowledge_base_id,
                embedding_model=embedding_model,
                embedding_revision=embedding_revision,
                dimension=dimension,
                metric=metric,
                faiss_path="pending",
            )
            db.add(index)
            db.flush()
            index.faiss_path = f"{knowledge_base_id}/{index.id}/index.faiss"
            db.flush()
    except IntegrityError:
        index = db.scalars(select(VectorIndex).where(*filters)).first()
        if index is None:
            raise
        return index

    return index


def stage_vector_mappings_for_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    embedding_model: str,
    embedding_revision: str,
    dimension: int,
    metric: str = "ip",
) -> StagedVectorMappings:
    """Assign staged vector ids to every chunk in the run. Safe to call on retry."""
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise ValueError(f"Processing run {run_id} was not found.")

    index = get_or_create_vector_index(
        db,
        knowledge_base_id=run.knowledge_base_id,
        embedding_model=embedding_model,
        embedding_revision=embedding_revision,
        dimension=dimension,
        metric=metric,
    )

    db.execute(select(VectorIndex).where(VectorIndex.id == index.id).with_for_update())
    db.refresh(index)

    chunks = list(
        db.scalars(select(Chunk).where(Chunk.processing_run_id == run_id).order_by(Chunk.sequence)).all()
    )
    if not chunks:
        return StagedVectorMappings(index_id=index.id, pairs=())

    chunk_ids = [chunk.id for chunk in chunks]
    existing_by_chunk = {
        mapping.chunk_id: mapping
        for mapping in db.scalars(
            select(VectorMapping).where(
                VectorMapping.index_id == index.id,
                VectorMapping.chunk_id.in_(chunk_ids),
            )
        ).all()
    }

    next_id = index.next_vector_id
    allocated = 0
    pairs: list[tuple[uuid.UUID, int]] = []

    for chunk in chunks:
        existing = existing_by_chunk.get(chunk.id)
        if existing is not None:
            pairs.append((chunk.id, existing.vector_id))
            continue

        db.add(
            VectorMapping(
                index_id=index.id,
                vector_id=next_id,
                chunk_id=chunk.id,
                state="staged",
            )
        )
        pairs.append((chunk.id, next_id))
        next_id += 1
        allocated += 1

    if allocated:
        index.next_vector_id = next_id

    db.flush()
    return StagedVectorMappings(index_id=index.id, pairs=tuple(pairs))