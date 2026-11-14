"""Activation prerequisites for a processing run."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk, ProcessingRun, VectorIndex, VectorMapping
from app.modules.chunking.service import chunks_complete
from app.modules.ingestion.index_stages import (
    embed_artifacts_complete,
    vector_index_artifacts_complete,
)
from app.modules.lexical import lexical_index_is_complete


def vector_index_id_for_run(db: Session, run: ProcessingRun) -> uuid.UUID | None:
    if run.embedding_model is None or run.embedding_revision is None or run.embedding_dimension is None:
        return None
    index = db.scalars(
        select(VectorIndex).where(
            VectorIndex.knowledge_base_id == run.knowledge_base_id,
            VectorIndex.embedding_model == run.embedding_model,
            VectorIndex.embedding_revision == run.embedding_revision,
            VectorIndex.dimension == run.embedding_dimension,
        )
    ).first()
    return index.id if index is not None else None


def run_has_committed_mappings_for_all_chunks(db: Session, *, run_id: uuid.UUID, index_id: uuid.UUID) -> bool:
    chunks = list(db.scalars(select(Chunk).where(Chunk.processing_run_id == run_id)).all())
    if not chunks:
        return True
    chunk_ids = [chunk.id for chunk in chunks]
    mappings = list(
        db.scalars(
            select(VectorMapping).where(
                VectorMapping.index_id == index_id,
                VectorMapping.chunk_id.in_(chunk_ids),
                VectorMapping.state == "committed",
            )
        ).all()
    )
    return len(mappings) == len(chunks)


def run_ready_for_activation(
    db: Session,
    run_id: uuid.UUID,
    *,
    require_committed_mappings: bool = True,
) -> bool:
    run = db.get(ProcessingRun, run_id)
    if run is None or run.status not in ("extracted", "ready", "embedding", "indexing"):
        return False
    if not chunks_complete(db, run_id):
        return False
    if not lexical_index_is_complete(db, run_id):
        return False
    index_id = vector_index_id_for_run(db, run)
    if index_id is None:
        return False
    if not embed_artifacts_complete(db, run_id=run_id):
        return False
    if not vector_index_artifacts_complete(db, run_id=run_id):
        return False
    if not require_committed_mappings:
        return True
    return run_has_committed_mappings_for_all_chunks(db, run_id=run_id, index_id=index_id)


def run_staged_for_activation(db: Session, run_id: uuid.UUID) -> bool:
    return run_ready_for_activation(db, run_id, require_committed_mappings=False)


def require_run_ready_for_activation(
    db: Session,
    run_id: uuid.UUID,
    *,
    require_committed_mappings: bool = True,
) -> uuid.UUID:
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise ValueError(f"Processing run {run_id} was not found.")
    index_id = vector_index_id_for_run(db, run)
    if index_id is None:
        raise ValueError("Embedding index metadata is missing on the run.")
    if not run_ready_for_activation(db, run_id, require_committed_mappings=require_committed_mappings):
        raise ValueError("Processing run is not ready for activation.")
    return index_id