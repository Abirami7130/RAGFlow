"""retire_run job handler."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Job
from app.modules.activation.retire import retire_processing_run


def handle_retire_run(db: Session, job: Job) -> None:
    run_id = uuid.UUID(str(job.payload["processing_run_id"]))
    retire_processing_run(db, run_id=run_id)
    db.commit()