"""Purge lexical, vector, and chunk rows for a deleted document."""

from __future__ import annotations

import uuid
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Chunk, ChunkStats, Document, LexicalPosting, ProcessingRun, VectorMapping
from app.modules.vectorstore.paths import sidecar_abs_path, staged_faiss_abs_path
from app.modules.vectorstore.live_publish import rebuild_live_index_from_committed


def purge_document(db: Session, *, document_id: uuid.UUID) -> bool:
    document = db.get(Document, document_id)
    if document is None:
        return True
    if document.deleted_at is None:
        raise ValueError("Document must be soft-deleted before purge.")

    runs = list(db.scalars(select(ProcessingRun).where(ProcessingRun.document_id == document_id)).all())
    run_ids = [run.id for run in runs]
    chunks = list(db.scalars(select(Chunk).where(Chunk.document_id == document_id)).all())
    chunk_ids = [chunk.id for chunk in chunks]

    if run_ids:
        db.execute(delete(LexicalPosting).where(LexicalPosting.processing_run_id.in_(run_ids)))
    if chunk_ids:
        db.execute(delete(ChunkStats).where(ChunkStats.chunk_id.in_(chunk_ids)))

    index_ids = list(
        db.scalars(
            select(VectorMapping.index_id).where(VectorMapping.chunk_id.in_(chunk_ids)).distinct()
        ).all()
    ) if chunk_ids else []

    for chunk in chunks:
        for index_id in index_ids:
            for run in runs:
                for path in (
                    sidecar_abs_path(
                        knowledge_base_id=chunk.knowledge_base_id,
                        index_id=index_id,
                        processing_run_id=run.id,
                    ),
                    staged_faiss_abs_path(
                        knowledge_base_id=chunk.knowledge_base_id,
                        index_id=index_id,
                        processing_run_id=run.id,
                    ),
                ):
                    path.unlink(missing_ok=True)

    if chunk_ids:
        db.execute(delete(VectorMapping).where(VectorMapping.chunk_id.in_(chunk_ids)))
    if chunk_ids:
        db.execute(delete(Chunk).where(Chunk.id.in_(chunk_ids)))

    for index_id in index_ids:
        rebuild_live_index_from_committed(db, index_id=index_id)

    db.flush()
    return True