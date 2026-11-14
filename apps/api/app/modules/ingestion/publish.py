"""Publish a normalized block set and the extraction lifecycle in one transaction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.extraction import NormalizedBlock
from app.models import CanonicalBlock, Document, DocumentVersion, ProcessingRun, QueryTrace, TraceEvent
from app.modules.chunking.stage import complete_chunk_stage
from app.modules.chunking.service import chunks_complete
from app.modules.activation.stage import complete_activate_stage
from app.modules.ingestion.index_stages import complete_post_chunk_index_stages, post_chunk_index_complete
from app.modules.ingestion.trace import append_skipped_stage, close_stage, finalize_trace_header, insert_skipped_tail
from app.worker.errors import PermanentWorkerError

ExistingBlockState = Literal["absent", "complete", "inconsistent"]


def _mark_extracted_preview(db: Session, *, locked: Document, run: ProcessingRun, version: DocumentVersion) -> None:
    run.is_active = False
    run.error_code = None
    run.error_detail = None
    run.finished_at = datetime.now(UTC)
    run.status = "extracted"
    if locked.current_version_id is None:
        locked.status = "extracted"
        locked.error_code = None
        locked.error_detail = None
        locked.preview_version_id = version.id
    elif locked.current_version_id != version.id:
        locked.preview_version_id = version.id


def _finalize_extracted_publish(
    db: Session,
    *,
    locked: Document,
    run: ProcessingRun,
    version: DocumentVersion,
    trace: QueryTrace,
) -> None:
    complete_post_chunk_index_stages(db, run=run, trace=trace)
    complete_activate_stage(db, document=locked, run=run, trace=trace)
    run.finished_at = datetime.now(UTC)
    finalize_trace_header(trace, status="succeeded")
    db.commit()


def _sequences(db: Session, version_id: uuid.UUID) -> list[int]:
    return list(
        db.scalars(
            select(CanonicalBlock.sequence)
            .where(CanonicalBlock.document_version_id == version_id)
            .order_by(CanonicalBlock.sequence)
        ).all()
    )


def existing_block_state(db: Session, version_id: uuid.UUID) -> ExistingBlockState:
    sequences = _sequences(db, version_id)
    if not sequences:
        return "absent"
    if sequences == list(range(len(sequences))):
        return "complete"
    return "inconsistent"


def blocks_complete(db: Session, version_id: uuid.UUID) -> bool:
    return existing_block_state(db, version_id) == "complete"


def preview_blocks_published(db: Session, *, document: Document, version_id: uuid.UUID) -> bool:
    current = db.scalar(
        select(Document).where(Document.id == document.id).execution_options(populate_existing=True)
    )
    if current is None or current.preview_version_id != version_id:
        return False
    return blocks_complete(db, version_id)


def finalize_existing_canonical_blocks(
    db: Session,
    *,
    document: Document,
    run: ProcessingRun,
    version: DocumentVersion,
    trace: QueryTrace,
    normalize_event: TraceEvent | None = None,
    record_early_skipped_stages: bool = False,
) -> str:
    """Finalize a pending ingest when canonical blocks are already complete. Returns finalized, replay, or stale."""
    try:
        return _finalize_existing(
            db,
            document=document,
            run=run,
            version=version,
            trace=trace,
            normalize_event=normalize_event,
            record_early_skipped_stages=record_early_skipped_stages,
        )
    except Exception:
        db.rollback()
        raise


def _finalize_existing(
    db: Session,
    *,
    document: Document,
    run: ProcessingRun,
    version: DocumentVersion,
    trace: QueryTrace,
    normalize_event: TraceEvent | None,
    record_early_skipped_stages: bool,
) -> str:
    locked = db.scalar(
        select(Document)
        .where(Document.id == document.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.deleted_at is not None:
        raise PermanentWorkerError("document_deleted", "Document was deleted.")

    state = existing_block_state(db, version.id)
    if state == "inconsistent":
        raise PermanentWorkerError(
            "inconsistent_blocks",
            "Canonical blocks for this version are incomplete or non-dense.",
        )
    if state != "complete":
        db.rollback()
        return "stale"

    if locked.pending_run_id is not None and locked.pending_run_id != run.id:
        db.rollback()
        return "stale"
    if locked.pending_run_id is None:
        if run.status == "extracted" and run.finished_at is not None:
            if chunks_complete(db, run.id) and post_chunk_index_complete(db, run.id):
                db.rollback()
                return "replay"
        elif locked.preview_version_id == version.id and chunks_complete(db, run.id):
            if post_chunk_index_complete(db, run.id):
                db.rollback()
                return "replay"
        elif locked.preview_version_id is not None and locked.preview_version_id != version.id:
            db.rollback()
            return "stale"

    if record_early_skipped_stages:
        append_skipped_stage(
            db,
            trace_id=trace.id,
            stage="extract",
            skip_reason="blocks_present",
            params={"source_type": document.source_type},
        )
        append_skipped_stage(
            db,
            trace_id=trace.id,
            stage="normalize",
            skip_reason="blocks_present",
            params={"source_type": document.source_type},
        )
    elif normalize_event is not None:
        close_stage(normalize_event, status="skipped", skip_reason="blocks_present")
        normalize_event.duration_ms = 0

    if locked.pending_run_id == run.id:
        locked.pending_version_id = None
        locked.pending_run_id = None

    complete_chunk_stage(
        db,
        document=locked,
        run=run,
        version=version,
        trace=trace,
    )
    _finalize_extracted_publish(db, locked=locked, run=run, version=version, trace=trace)
    return "finalized"


def publish_normalized_blocks(
    db: Session,
    *,
    document: Document,
    run: ProcessingRun,
    version: DocumentVersion,
    trace: QueryTrace,
    normalize_event: TraceEvent,
    blocks: list[NormalizedBlock],
    normalize_ms: int,
) -> str:
    """Insert blocks and advance lifecycle. Returns published, finalized, replay, or stale."""
    try:
        return _publish(
            db,
            document=document,
            run=run,
            version=version,
            trace=trace,
            normalize_event=normalize_event,
            blocks=blocks,
            normalize_ms=normalize_ms,
        )
    except Exception:
        db.rollback()
        raise


def _publish(
    db: Session,
    *,
    document: Document,
    run: ProcessingRun,
    version: DocumentVersion,
    trace: QueryTrace,
    normalize_event: TraceEvent,
    blocks: list[NormalizedBlock],
    normalize_ms: int,
) -> str:
    locked = db.scalar(
        select(Document)
        .where(Document.id == document.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.deleted_at is not None:
        raise PermanentWorkerError("document_deleted", "Document was deleted.")

    state = existing_block_state(db, version.id)
    if state == "inconsistent":
        raise PermanentWorkerError(
            "inconsistent_blocks",
            "Canonical blocks for this version are incomplete or non-dense.",
        )
    if state == "complete":
        return _finalize_existing(
            db,
            document=document,
            run=run,
            version=version,
            trace=trace,
            normalize_event=normalize_event,
            record_early_skipped_stages=False,
        )

    if locked.pending_run_id != run.id:
        db.rollback()
        return "stale"

    for block in blocks:
        db.add(
            CanonicalBlock(
                document_version_id=version.id,
                sequence=block.sequence,
                block_type=block.block_type,
                text=block.text,
                locator=block.locator,
                block_metadata=block.metadata,
            )
        )
    db.flush()

    if locked.pending_run_id == run.id:
        locked.pending_version_id = None
        locked.pending_run_id = None

    close_stage(normalize_event, status="succeeded", output={"block_count": len(blocks)})
    normalize_event.duration_ms = normalize_ms
    complete_chunk_stage(
        db,
        document=locked,
        run=run,
        version=version,
        trace=trace,
    )
    _finalize_extracted_publish(db, locked=locked, run=run, version=version, trace=trace)
    return "published"
