"""Atomic document activation and rollback."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import Document, ProcessingRun
from app.modules.activation.active_chunks import active_chunk_ids_for_knowledge_base
from app.modules.activation.retire import enqueue_retire_runs, plan_retirements_after_activation
from app.modules.lexical import recompute_lexical_corpus_for_active_chunks


@dataclass(frozen=True, slots=True)
class ActivationResult:
    document_id: uuid.UUID
    processing_run_id: uuid.UUID
    version_id: uuid.UUID
    replaced_version_id: uuid.UUID | None


def _recompute_kb_corpus(db: Session, *, knowledge_base_id: uuid.UUID) -> None:
    recompute_lexical_corpus_for_active_chunks(
        db,
        knowledge_base_id,
        active_chunk_ids_for_knowledge_base(db, knowledge_base_id),
    )


def activate_processing_run(db: Session, *, run_id: uuid.UUID) -> ActivationResult:
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise ValueError(f"Processing run {run_id} was not found.")

    document = db.scalar(select(Document).where(Document.id == run.document_id).with_for_update())
    if document is None or document.deleted_at is not None:
        raise ValueError("Document was not found.")

    if document.pending_run_id not in (None, run.id):
        raise ValueError("This run is no longer the pending run for the document.")
    if document.preview_version_id not in (None, run.document_version_id):
        if document.current_version_id != run.document_version_id:
            raise ValueError("This run does not match the document preview version.")

    previous_version_id = document.current_version_id
    now = datetime.now(UTC)

    current_active = db.scalar(
        select(ProcessingRun).where(
            ProcessingRun.document_id == document.id,
            ProcessingRun.is_active.is_(True),
        )
    )
    if current_active is not None and current_active.id != run.id:
        current_active.is_active = False
        current_active.superseded_at = now
        db.flush()

    run.is_active = True
    run.status = "ready"
    run.superseded_at = None
    document.current_version_id = run.document_version_id
    document.preview_version_id = None
    document.pending_version_id = None
    document.pending_run_id = None
    document.status = "ready"
    document.error_code = None
    document.error_detail = None

    _recompute_kb_corpus(db, knowledge_base_id=document.knowledge_base_id)
    retire_ids = plan_retirements_after_activation(db, document_id=document.id)
    if retire_ids:
        enqueue_retire_runs(db, knowledge_base_id=document.knowledge_base_id, run_ids=retire_ids)
    db.flush()
    return ActivationResult(
        document_id=document.id,
        processing_run_id=run.id,
        version_id=run.document_version_id,
        replaced_version_id=previous_version_id,
    )


def rollback_document_serving(db: Session, *, document_id: uuid.UUID, user_id: uuid.UUID) -> Document:
    from app.modules.ingestion.service import _require_editor

    document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if document is None or document.deleted_at is not None:
        raise APIError(404, "not_found", "Document not found.")
    _require_editor(db, document.knowledge_base_id, user_id)

    if document.status != "ready" or document.current_version_id is None:
        raise APIError(422, "unprocessable", "There is no serving version to roll back.")

    current_active = db.scalar(
        select(ProcessingRun).where(
            ProcessingRun.document_id == document.id,
            ProcessingRun.is_active.is_(True),
        )
    )
    if current_active is None:
        raise APIError(422, "unprocessable", "There is no active processing run.")

    previous = db.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.document_id == document.id,
            ProcessingRun.status == "ready",
            ProcessingRun.is_active.is_(False),
            ProcessingRun.superseded_at.is_not(None),
        )
        .order_by(ProcessingRun.superseded_at.desc())
    )
    if previous is None:
        raise APIError(422, "unprocessable", "There is no previous ready run to restore.")
    if previous.retired_at is not None:
        raise APIError(409, "version_retired", "The previous ready run has been retired.")

    now = datetime.now(UTC)
    current_active.is_active = False
    current_active.superseded_at = now
    db.flush()
    previous.is_active = True
    previous.superseded_at = None

    document.current_version_id = previous.document_version_id
    document.preview_version_id = None
    document.status = "ready"

    _recompute_kb_corpus(db, knowledge_base_id=document.knowledge_base_id)
    db.flush()
    return document