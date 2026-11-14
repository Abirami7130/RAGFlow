"""purge_document job handler."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Job
from app.modules.activation.purge import purge_document
from app.worker.errors import PermanentWorkerError


def handle_purge_document(db: Session, job: Job) -> None:
    document_id = uuid.UUID(str(job.payload["document_id"]))
    if not purge_document(db, document_id=document_id):
        raise PermanentWorkerError("not_found", "Document was not found.")
    db.commit()