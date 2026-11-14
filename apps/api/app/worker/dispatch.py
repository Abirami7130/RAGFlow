"""Dispatch claimed jobs to handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models import Job
from app.worker.errors import AUTOMATIC_RETRY_DELAYS_SECONDS, PERMANENT_ERROR_CODES, PermanentWorkerError, TransientWorkerError
from app.worker.claim import mark_job_failed, mark_job_succeeded, schedule_automatic_retry
from app.worker.handlers.ingest import handle_ingest_document, persist_ingest_failure
from app.worker.handlers.noop import handle_noop
from app.worker.handlers.purge import handle_purge_document
from app.worker.handlers.retire import handle_retire_run

Handler = Callable[[Session, Job], None]

HANDLERS: dict[str, Handler] = {
    "ingest_document": handle_ingest_document,
    "noop": handle_noop,
    "purge_document": handle_purge_document,
    "retire_run": handle_retire_run,
}


def execute_job(db: Session, job: Job) -> None:
    handler = HANDLERS.get(job.job_type)
    if handler is None:
        raise PermanentWorkerError("not_found", f"No handler for job type {job.job_type!r}.")
    try:
        handler(db, job)
        db.refresh(job)
        if job.status == "failed":
            return
        mark_job_succeeded(db, job)
    except TransientWorkerError as exc:
        if job.attempts < job.max_attempts:
            delay_index = min(max(job.attempts - 1, 0), len(AUTOMATIC_RETRY_DELAYS_SECONDS) - 1)
            schedule_automatic_retry(
                db,
                job,
                error_code=exc.error_code,
                error_message=exc.message,
                delay_seconds=AUTOMATIC_RETRY_DELAYS_SECONDS[delay_index],
            )
            return
        mark_job_failed(db, job, error_code=exc.error_code, error_message=exc.message)
        if job.job_type == "ingest_document":
            persist_ingest_failure(db, job, error_code=exc.error_code, error_message=exc.message)
    except PermanentWorkerError as exc:
        mark_job_failed(db, job, error_code=exc.error_code, error_message=exc.message)
        if job.job_type == "ingest_document" and exc.error_code in PERMANENT_ERROR_CODES:
            persist_ingest_failure(db, job, error_code=exc.error_code, error_message=exc.message)
    except Exception as exc:
        mark_job_failed(db, job, error_code="unexpected_error", error_message=str(exc))
        if job.job_type == "ingest_document":
            persist_ingest_failure(db, job, error_code="unexpected_error", error_message=str(exc))
