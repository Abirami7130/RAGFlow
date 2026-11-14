"""Document and run lifecycle updates for ingestion outcomes."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, Job, ProcessingRun, QueryTrace
from app.modules.ingestion.trace import ensure_complete_trace_tail, finalize_trace_header


def commit_ingest_failure(
    db: Session,
    *,
    document: Document,
    run: ProcessingRun,
    job: Job,
    trace: QueryTrace,
    error_code: str,
    error_detail: str,
) -> None:
    document = db.scalar(
        select(Document)
        .where(Document.id == document.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if document is None:
        return
    owns_pending = document.pending_run_id == run.id
    if owns_pending:
        document.pending_version_id = None
        document.pending_run_id = None
    run.status = "failed"
    run.error_code = error_code
    run.error_detail = error_detail
    job.status = "failed"
    job.last_error_code = error_code
    job.last_error = error_detail
    job.locked_at = None
    job.locked_by = None
    if owns_pending and document.current_version_id is None:
        if document.preview_version_id is not None:
            document.status = "extracted"
        else:
            document.status = "failed"
            document.error_code = error_code
            document.error_detail = error_detail
    elif document.current_version_id is not None:
        document.status = "ready"
        document.error_code = None
        document.error_detail = None
    finalize_trace_header(trace, status="failed", error_code=error_code)
    ensure_complete_trace_tail(db, trace)
    db.commit()


def commit_inconsistent_state(
    db: Session,
    *,
    document: Document,
    run: ProcessingRun,
    job: Job,
    trace: QueryTrace,
) -> None:
    commit_ingest_failure(
        db,
        document=document,
        run=run,
        job=job,
        trace=trace,
        error_code="inconsistent_state",
        error_detail="The serving version's processing run is not active.",
    )
