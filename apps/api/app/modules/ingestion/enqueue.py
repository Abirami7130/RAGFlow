"""Create processing runs, traces, and ingest jobs for stored versions."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Document, DocumentVersion, Job, KnowledgeBase, ProcessingRun, QueryTrace
from app.worker.errors import INGEST_MAX_ATTEMPTS


def trace_config(kb: KnowledgeBase, *, source_type: str, original_filename: str) -> dict:
    return {
        "source_type": source_type,
        "chunk_strategy": kb.chunk_config,
        "retrieval_config": kb.retrieval_config,
        "original_filename": original_filename,
    }


def enqueue_ingest(
    db: Session,
    *,
    kb: KnowledgeBase,
    document: Document,
    version: DocumentVersion,
    user_id: uuid.UUID,
    source_type: str,
    display_filename: str,
    original_filename: str,
    user_retry_count: int = 0,
    user_retry_limit: int = 3,
    payload_extras: dict | None = None,
) -> Job:
    trace = QueryTrace(
        knowledge_base_id=kb.id,
        kind="ingestion",
        user_id=user_id,
        processing_run_id=None,
        question=display_filename,
        status="running",
        config=trace_config(kb, source_type=source_type, original_filename=original_filename),
    )
    db.add(trace)
    db.flush()

    run = ProcessingRun(
        document_id=document.id,
        document_version_id=version.id,
        knowledge_base_id=kb.id,
        status="queued",
        chunk_strategy=kb.chunk_config,
        trace_id=trace.id,
        user_retry_count=user_retry_count,
        user_retry_limit=user_retry_limit,
    )
    db.add(run)
    db.flush()

    trace.processing_run_id = run.id
    payload = {"processing_run_id": str(run.id), "document_version_id": str(version.id)}
    if payload_extras:
        payload.update(payload_extras)
    job = Job(
        job_type="ingest_document",
        idempotency_key=f"processing_run:{run.id}",
        knowledge_base_id=kb.id,
        payload=payload,
        status="pending",
        max_attempts=INGEST_MAX_ATTEMPTS,
    )
    db.add(job)
    db.flush()

    document.pending_version_id = version.id
    document.pending_run_id = run.id
    db.flush()
    return job
