"""User-initiated ingest retries (force upload and reprocess)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import Document, DocumentVersion, Job, KnowledgeBase, KnowledgeBaseMember, ProcessingRun
from app.modules.ingestion.enqueue import enqueue_ingest

USER_RETRY_LIMIT = 3


def _require_editor(db: Session, kb_id: uuid.UUID, user_id: uuid.UUID) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.deleted_at is not None:
        raise APIError(404, "not_found", "Knowledge base not found.")
    member = db.get(KnowledgeBaseMember, (kb_id, user_id))
    if member is None:
        raise APIError(404, "not_found", "Knowledge base not found.")
    if member.role == "viewer":
        raise APIError(403, "forbidden", "You do not have access to this action.")
    return kb


def _live_ingest_job(db: Session, document: Document) -> Job | None:
    if document.pending_run_id is None:
        return None
    return db.scalar(
        select(Job).where(
            Job.job_type == "ingest_document",
            Job.status.in_(("pending", "running")),
            Job.idempotency_key == f"processing_run:{document.pending_run_id}",
        )
    )


def _max_user_retry_count(db: Session, *, document_id: uuid.UUID, version_id: uuid.UUID) -> int:
    value = db.scalar(
        select(func.max(ProcessingRun.user_retry_count)).where(
            ProcessingRun.document_id == document_id,
            ProcessingRun.document_version_id == version_id,
        )
    )
    return value or 0


def _has_inconsistent_state(db: Session, *, document_id: uuid.UUID, version_id: uuid.UUID) -> bool:
    run = db.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.document_id == document_id,
            ProcessingRun.document_version_id == version_id,
            ProcessingRun.error_code == "inconsistent_state",
        )
        .limit(1)
    )
    return run is not None


def request_user_retry_on_version(
    db: Session,
    *,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    user_id: uuid.UUID,
    allow_extracted_preview: bool = False,
) -> Job:
    document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if document is None or document.deleted_at is not None:
        raise APIError(404, "not_found", "Document not found.")
    kb = _require_editor(db, document.knowledge_base_id, user_id)
    version = db.get(DocumentVersion, version_id)
    if version is None or version.document_id != document.id:
        raise APIError(404, "not_found", "Document version not found.")

    live_job = _live_ingest_job(db, document)
    if live_job is not None:
        raise APIError(
            409,
            "ingest_in_progress",
            "A document ingest is already in progress.",
            {"job_id": str(live_job.id)},
        )

    if _has_inconsistent_state(db, document_id=document.id, version_id=version.id):
        raise APIError(
            422,
            "unprocessable",
            "This version cannot be retried after an inconsistent_state failure.",
        )

    if not allow_extracted_preview and document.preview_version_id == version.id:
        latest_run = db.scalar(
            select(ProcessingRun)
            .where(
                ProcessingRun.document_id == document.id,
                ProcessingRun.document_version_id == version.id,
            )
            .order_by(ProcessingRun.created_at.desc())
        )
        if latest_run is not None and latest_run.status == "extracted":
            raise APIError(
                422,
                "unprocessable",
                "This version is already the current preview.",
            )

    next_user_retry = _max_user_retry_count(db, document_id=document.id, version_id=version.id) + 1
    if next_user_retry > USER_RETRY_LIMIT:
        raise APIError(
            422,
            "retry_limit_reached",
            "The user retry limit for this version has been reached.",
            {"user_retry_limit": USER_RETRY_LIMIT},
        )

    original = version.original_filename or document.title
    job = enqueue_ingest(
        db,
        kb=kb,
        document=document,
        version=version,
        user_id=user_id,
        source_type=document.source_type,
        display_filename=document.title,
        original_filename=original,
        user_retry_count=next_user_retry,
        user_retry_limit=USER_RETRY_LIMIT,
    )
    db.commit()
    return job
