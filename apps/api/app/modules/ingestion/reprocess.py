"""Reprocess endpoint logic."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import Document
from app.modules.ingestion.user_retry import request_user_retry_on_version


def reprocess_document(db: Session, *, document_id: uuid.UUID, user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    document = db.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise APIError(404, "not_found", "Document not found.")
    if document.current_version_id is None:
        raise APIError(422, "unprocessable", "There is no serving version to reprocess.")
    job = request_user_retry_on_version(
        db,
        document_id=document_id,
        version_id=document.current_version_id,
        user_id=user_id,
        allow_extracted_preview=True,
    )
    run_id = uuid.UUID(str(job.payload["processing_run_id"]))
    from app.models import ProcessingRun

    run = db.get(ProcessingRun, run_id)
    if run is None or run.trace_id is None:
        raise APIError(409, "conflict", "Reprocess could not be scheduled.")
    return run.id, run.trace_id, job.id
