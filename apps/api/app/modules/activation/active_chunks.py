"""Active chunk membership for retrieval and corpus stats."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk, Document, ProcessingRun


def active_chunk_ids_for_knowledge_base(db: Session, knowledge_base_id: uuid.UUID) -> list[uuid.UUID]:
    """Chunks on serving, non-deleted documents with an active processing run."""
    rows = db.execute(
        select(Chunk.id)
        .join(ProcessingRun, ProcessingRun.id == Chunk.processing_run_id)
        .join(Document, Document.id == ProcessingRun.document_id)
        .where(
            Chunk.knowledge_base_id == knowledge_base_id,
            ProcessingRun.knowledge_base_id == knowledge_base_id,
            ProcessingRun.is_active.is_(True),
            ProcessingRun.status == "ready",
            Document.deleted_at.is_(None),
            Document.status == "ready",
            Document.current_version_id == ProcessingRun.document_version_id,
        )
        .order_by(Chunk.id)
    ).all()
    return [row[0] for row in rows]