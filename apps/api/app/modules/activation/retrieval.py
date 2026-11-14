"""Committed-only vector lookup for active serving documents."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk, Document, ProcessingRun, VectorMapping


def vector_mapping_visible_in_search(db: Session, *, index_id: uuid.UUID, vector_id: int) -> bool:
    mapping = db.scalar(
        select(VectorMapping).where(
            VectorMapping.index_id == index_id,
            VectorMapping.vector_id == vector_id,
            VectorMapping.state == "committed",
        )
    )
    if mapping is None:
        return False
    chunk = db.get(Chunk, mapping.chunk_id)
    if chunk is None:
        return False
    run = db.get(ProcessingRun, chunk.processing_run_id)
    if run is None or not run.is_active or run.status != "ready":
        return False
    document = db.get(Document, run.document_id)
    if document is None or document.deleted_at is not None or document.status != "ready":
        return False
    return document.current_version_id == run.document_version_id