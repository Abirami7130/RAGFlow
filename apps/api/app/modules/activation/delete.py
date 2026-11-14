"""Soft-delete a serving document and enqueue durable purge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import Document, Job, ProcessingRun
from app.modules.activation.active_chunks import active_chunk_ids_for_knowledge_base
from app.modules.lexical import recompute_lexical_corpus_for_active_chunks


def enqueue_purge_document(db: Session, *, document_id: uuid.UUID, knowledge_base_id: uuid.UUID) -> Job:
    key = f"purge:{document_id}"
    existing = db.scalar(
        select(Job).where(
            Job.job_type == "purge_document",
            Job.idempotency_key == key,
            Job.status.in_(("pending", "running")),
        )
    )
    if existing is not None:
        return existing
    job = Job(
        job_type="purge_document",
        idempotency_key=key,
        knowledge_base_id=knowledge_base_id,
        payload={"document_id": str(document_id)},
        status="pending",
    )
    db.add(job)
    db.flush()
    return job


def soft_delete_document(db: Session, *, document_id: uuid.UUID, user_id: uuid.UUID) -> Job:
    from app.modules.ingestion.service import _require_editor

    document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if document is None or document.deleted_at is not None:
        raise APIError(404, "not_found", "Document not found.")
    _require_editor(db, document.knowledge_base_id, user_id)

    now = datetime.now(UTC)
    document.deleted_at = now
    active_runs = list(
        db.scalars(
            select(ProcessingRun).where(
                ProcessingRun.document_id == document.id,
                ProcessingRun.is_active.is_(True),
            )
        ).all()
    )
    for run in active_runs:
        run.is_active = False

    recompute_lexical_corpus_for_active_chunks(
        db,
        document.knowledge_base_id,
        active_chunk_ids_for_knowledge_base(db, document.knowledge_base_id),
    )
    job = enqueue_purge_document(db, document_id=document.id, knowledge_base_id=document.knowledge_base_id)
    db.flush()
    return job