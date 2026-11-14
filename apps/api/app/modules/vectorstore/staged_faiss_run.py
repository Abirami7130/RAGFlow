"""Build and validate staged FAISS artifacts for one processing run."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ProcessingRun, VectorIndex
from app.modules.vectorstore.paths import sidecar_abs_path, staged_faiss_abs_path, staged_faiss_rel_path
from app.modules.vectorstore.sidecar import load_sidecar, sidecar_is_complete, staged_vector_ids_for_run
from app.modules.vectorstore.staged_faiss import (
    build_staged_faiss_from_sidecar,
    round_trip_sidecar_search,
    staged_faiss_is_complete,
    validate_staged_faiss_file,
)


@dataclass(frozen=True, slots=True)
class StagedFaissArtifacts:
    index_id: uuid.UUID
    staged_faiss_path: str
    vector_ids: tuple[int, ...]
    faiss_replaced: bool


def build_staged_faiss_artifacts_for_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    index_id: uuid.UUID,
    force: bool = False,
) -> StagedFaissArtifacts:
    """Read the validated sidecar, build staged FAISS, and confirm ids/count/dimension match staged mappings."""
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise ValueError(f"Processing run {run_id} was not found.")
    index = db.get(VectorIndex, index_id)
    if index is None:
        raise ValueError(f"Vector index {index_id} was not found.")
    if index.knowledge_base_id != run.knowledge_base_id:
        raise ValueError("Vector index does not belong to the processing run knowledge base.")

    expected_ids = staged_vector_ids_for_run(db, run_id=run_id, index_id=index_id)
    dimension = index.dimension
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

    if not sidecar_is_complete(sidecar_path, expected_ids=expected_ids, dimension=dimension):
        raise ValueError("Vector sidecar is missing or does not match staged mappings.")

    payload = load_sidecar(sidecar_path)
    if payload.ids != expected_ids:
        raise ValueError("Vector sidecar ids do not match staged mappings.")
    if payload.dimension != dimension:
        raise ValueError("Vector sidecar dimension does not match the vector index.")
    if len(payload.vectors) != len(expected_ids):
        raise ValueError("Vector sidecar vector count does not match staged mappings.")

    faiss_was_complete = staged_faiss_is_complete(
        staged_path,
        dimension=dimension,
        expected_ids=expected_ids,
    )

    build_staged_faiss_from_sidecar(sidecar_path, staged_path=staged_path, force=force)

    if not staged_faiss_is_complete(staged_path, dimension=dimension, expected_ids=expected_ids):
        raise ValueError("Staged FAISS artifact is incomplete after build.")

    validated_index = validate_staged_faiss_file(
        staged_path,
        dimension=dimension,
        expected_ids=expected_ids,
    )
    if validated_index.dimension != dimension or validated_index.size != len(expected_ids):
        raise ValueError("Staged FAISS artifact does not match staged mappings.")
    round_trip_sidecar_search(staged_path, payload)

    return StagedFaissArtifacts(
        index_id=index_id,
        staged_faiss_path=staged_faiss_rel_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=index_id,
            processing_run_id=run_id,
        ),
        vector_ids=expected_ids,
        faiss_replaced=force or not faiss_was_complete,
    )