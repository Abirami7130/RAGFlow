"""PostgreSQL-backed job claiming."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Job

logger = logging.getLogger(__name__)

CLAIM_SQL = text(
    """
    UPDATE jobs
    SET status = 'running',
        locked_by = :worker_id,
        locked_at = now(),
        attempts = attempts + 1,
        updated_at = now()
    WHERE id = (
        SELECT id
        FROM jobs
        WHERE (
            status = 'pending' AND run_after <= now()
        ) OR (
            status = 'running'
            AND locked_at IS NOT NULL
            AND locked_at < now() - make_interval(secs => lock_timeout_seconds)
        )
        ORDER BY run_after ASC, created_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING *
    """
)


def claim_next_job(db: Session, *, worker_id: str) -> Job | None:
    row = db.execute(CLAIM_SQL, {"worker_id": worker_id}).mappings().first()
    if row is None:
        db.rollback()
        return None
    job = db.get(Job, row["id"])
    db.commit()
    if job is not None:
        logger.info("job_claimed job_id=%s type=%s attempt=%s", job.id, job.job_type, job.attempts)
    return job


def refresh_job_lock(db: Session, *, job_id: uuid.UUID, worker_id: str) -> bool:
    result = db.execute(
        text(
            """
            UPDATE jobs
            SET locked_at = now(), updated_at = now()
            WHERE id = :job_id
              AND status = 'running'
              AND locked_by = :worker_id
            """
        ),
        {"job_id": job_id, "worker_id": worker_id},
    )
    db.commit()
    return result.rowcount == 1


def release_job_lock(
    db: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str,
) -> None:
    db.execute(
        text(
            """
            UPDATE jobs
            SET locked_at = NULL, locked_by = NULL, updated_at = now()
            WHERE id = :job_id AND locked_by = :worker_id AND status = 'running'
            """
        ),
        {"job_id": job_id, "worker_id": worker_id},
    )
    db.commit()


def mark_job_succeeded(db: Session, job: Job) -> None:
    job.status = "succeeded"
    job.locked_at = None
    job.locked_by = None
    job.last_error = None
    job.last_error_code = None
    job.updated_at = datetime.now(UTC)
    db.commit()


def schedule_automatic_retry(
    db: Session,
    job: Job,
    *,
    error_code: str,
    error_message: str,
    delay_seconds: int,
) -> None:
    job.status = "pending"
    job.locked_at = None
    job.locked_by = None
    job.last_error_code = error_code
    job.last_error = error_message
    job.run_after = datetime.now(UTC) + __import__("datetime").timedelta(seconds=delay_seconds)
    job.updated_at = datetime.now(UTC)
    db.commit()


def mark_job_failed(
    db: Session,
    job: Job,
    *,
    error_code: str,
    error_message: str,
) -> None:
    job.status = "failed"
    job.locked_at = None
    job.locked_by = None
    job.last_error_code = error_code
    job.last_error = error_message
    job.updated_at = datetime.now(UTC)
    db.commit()
