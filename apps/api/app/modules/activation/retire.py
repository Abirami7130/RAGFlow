"""Retire older ready runs and remove their vectors from the live index."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import Chunk, Job, LexicalPosting, ProcessingRun, VectorMapping
from app.modules.activation.active_chunks import active_chunk_ids_for_knowledge_base
from app.modules.lexical import recompute_lexical_corpus_for_active_chunks
from app.modules.vectorstore.live_publish import rebuild_live_index_from_committed


def plan_retirements_after_activation(db: Session, *, document_id: uuid.UUID) -> list[uuid.UUID]:
    """Return run ids to retire, keeping the active run and the immediate previous ready run."""
    ready_runs = list(
        db.scalars(
            select(ProcessingRun)
            .where(
                ProcessingRun.document_id == document_id,
                ProcessingRun.status == "ready",
                ProcessingRun.retired_at.is_(None),
            )
            .order_by(ProcessingRun.created_at.desc())
        ).all()
    )
    if len(ready_runs) <= 2:
        return []
    return [run.id for run in ready_runs[2:]]


def enqueue_retire_runs(db: Session, *, knowledge_base_id: uuid.UUID, run_ids: list[uuid.UUID]) -> None:
    for run_id in run_ids:
        key = f"retire:{run_id}"
        existing = db.scalar(
            select(Job).where(
                Job.job_type == "retire_run",
                Job.idempotency_key == key,
                Job.status.in_(("pending", "running")),
            )
        )
        if existing is not None:
            continue
        db.add(
            Job(
                job_type="retire_run",
                idempotency_key=key,
                knowledge_base_id=knowledge_base_id,
                payload={"processing_run_id": str(run_id)},
                status="pending",
            )
        )
    db.flush()


def retire_processing_run(db: Session, *, run_id: uuid.UUID) -> bool:
    run = db.get(ProcessingRun, run_id)
    if run is None:
        return False
    if run.retired_at is not None:
        return False
    if run.is_active:
        raise ValueError("Cannot retire the active processing run.")

    chunk_ids = list(db.scalars(select(Chunk.id).where(Chunk.processing_run_id == run_id)).all())
    index_ids: list[uuid.UUID] = []
    if chunk_ids:
        index_ids = list(
            db.scalars(
                select(VectorMapping.index_id)
                .where(VectorMapping.chunk_id.in_(chunk_ids))
                .distinct()
            ).all()
        )
        db.execute(
            update(VectorMapping)
            .where(
                VectorMapping.chunk_id.in_(chunk_ids),
                VectorMapping.state == "committed",
            )
            .values(state="retired")
        )
        db.execute(delete(LexicalPosting).where(LexicalPosting.processing_run_id == run_id))

    for index_id in index_ids:
        rebuild_live_index_from_committed(db, index_id=index_id)

    run.is_active = False
    run.retired_at = datetime.now(UTC)
    recompute_lexical_corpus_for_active_chunks(
        db,
        run.knowledge_base_id,
        active_chunk_ids_for_knowledge_base(db, run.knowledge_base_id),
    )
    db.flush()
    return True