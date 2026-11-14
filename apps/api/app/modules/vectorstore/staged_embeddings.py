"""Staged embedding artifacts for a processing run (mappings + sidecar + run metadata)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ProcessingRun
from app.modules.embeddings.protocol import EmbeddingProvider
from app.modules.vectorstore.allocation import stage_vector_mappings_for_run
from app.modules.vectorstore.paths import sidecar_abs_path
from app.modules.vectorstore.sidecar import persist_run_sidecar, sidecar_is_complete


@dataclass(frozen=True, slots=True)
class StagedEmbeddingArtifacts:
    index_id: uuid.UUID
    sidecar_path: str
    vector_ids: tuple[int, ...]
    sidecar_replaced: bool


def _assert_run_embedding_identity(run: ProcessingRun, provider: EmbeddingProvider) -> None:
    if run.embedding_model is None:
        return
    if (
        run.embedding_model != provider.model_id
        or run.embedding_revision != provider.revision
        or run.embedding_dimension != provider.dimension
    ):
        raise ValueError("Processing run embedding identity does not match the provider.")


def _record_run_embedding_identity(run: ProcessingRun, provider: EmbeddingProvider) -> None:
    run.embedding_model = provider.model_id
    run.embedding_revision = provider.revision
    run.embedding_dimension = provider.dimension


def _sidecar_is_complete_for_run(
    *,
    path,
    vector_ids: tuple[int, ...],
    dimension: int,
) -> bool:
    return sidecar_is_complete(path, expected_ids=vector_ids, dimension=dimension)


def build_staged_embedding_artifacts_for_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    provider: EmbeddingProvider,
    metric: str = "ip",
    force: bool = False,
) -> StagedEmbeddingArtifacts:
    """Stage vector mappings, embed chunks, write the sidecar, then persist run embedding metadata."""
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise ValueError(f"Processing run {run_id} was not found.")
    _assert_run_embedding_identity(run, provider)

    staged = stage_vector_mappings_for_run(
        db,
        run_id=run_id,
        embedding_model=provider.model_id,
        embedding_revision=provider.revision,
        dimension=provider.dimension,
        metric=metric,
    )

    vector_ids = tuple(vector_id for _, vector_id in staged.pairs)
    dest = sidecar_abs_path(
        knowledge_base_id=run.knowledge_base_id,
        index_id=staged.index_id,
        processing_run_id=run_id,
    )
    sidecar_was_complete = _sidecar_is_complete_for_run(
        path=dest,
        vector_ids=vector_ids,
        dimension=provider.dimension,
    )

    sidecar_path = persist_run_sidecar(
        db,
        run_id=run_id,
        index_id=staged.index_id,
        provider=provider,
        force=force,
    )

    if not _sidecar_is_complete_for_run(path=dest, vector_ids=vector_ids, dimension=provider.dimension):
        raise ValueError("Vector sidecar is incomplete after persist.")

    _record_run_embedding_identity(run, provider)
    db.flush()

    return StagedEmbeddingArtifacts(
        index_id=staged.index_id,
        sidecar_path=sidecar_path,
        vector_ids=vector_ids,
        sidecar_replaced=force or not sidecar_was_complete,
    )