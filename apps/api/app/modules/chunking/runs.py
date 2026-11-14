"""Processing run read helpers."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import KnowledgeBaseMember, ProcessingRun
from app.schemas import ProcessingRunRead


def get_processing_run(db: Session, *, run_id: uuid.UUID, user_id: uuid.UUID) -> ProcessingRunRead:
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise APIError(404, "not_found", "Processing run not found.")
    member = db.get(KnowledgeBaseMember, (run.knowledge_base_id, user_id))
    if member is None:
        raise APIError(404, "not_found", "Processing run not found.")
    return ProcessingRunRead(
        id=run.id,
        document_version_id=run.document_version_id,
        status=run.status,
        is_active=run.is_active,
        superseded_at=run.superseded_at,
        retired_at=run.retired_at,
        chunk_strategy=run.chunk_strategy,
        embedding_model=run.embedding_model,
        embedding_revision=run.embedding_revision,
        embedding_dimension=run.embedding_dimension,
        chunk_count=run.chunk_count,
        trace_id=run.trace_id,
        error_code=run.error_code,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )