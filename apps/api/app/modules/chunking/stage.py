"""Ingestion chunk stage orchestration."""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CanonicalBlock, Document, DocumentVersion, ProcessingRun, QueryTrace, TraceEvent
from app.modules.chunking.service import chunks_complete, replace_chunks_for_run
from app.modules.ingestion.trace import close_stage, open_stage


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def complete_chunk_stage(
    db: Session,
    *,
    document: Document,
    run: ProcessingRun,
    version: DocumentVersion,
    trace: QueryTrace,
    chunk_event: TraceEvent | None = None,
) -> int:
    """Persist chunks for a run when missing. Returns chunk count."""
    owns_event = chunk_event is None
    if chunk_event is None:
        run.status = "chunking"
        chunk_event = open_stage(
            db,
            trace_id=trace.id,
            stage="chunk",
            params={"strategy": run.chunk_strategy.get("strategy", "structure_aware")},
        )

    started = time.perf_counter()
    if chunks_complete(db, run.id):
        close_stage(chunk_event, status="skipped", skip_reason="chunks_present")
        chunk_event.duration_ms = 0
        return run.chunk_count

    blocks = list(
        db.scalars(
            select(CanonicalBlock)
            .where(CanonicalBlock.document_version_id == version.id)
            .order_by(CanonicalBlock.sequence)
        ).all()
    )
    if not blocks:
        raise ValueError("Canonical blocks are required before chunking.")

    drafts = replace_chunks_for_run(
        db,
        run=run,
        version_id=version.id,
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        blocks=blocks,
        strategy=run.chunk_strategy,
    )
    close_stage(
        chunk_event,
        status="succeeded",
        output={"chunk_count": len(drafts), "strategy": run.chunk_strategy.get("strategy")},
    )
    chunk_event.duration_ms = _elapsed_ms(started)
    run.status = "extracted"
    if owns_event:
        db.flush()
    return len(drafts)