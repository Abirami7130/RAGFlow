"""ingest_document job handler."""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.extraction import ExtractionError
from app.models import Document, DocumentVersion, Job, KnowledgeBase, ProcessingRun, QueryTrace, TraceEvent
from app.modules.ingestion import storage as blob_storage
from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks
from app.modules.ingestion.lifecycle import commit_inconsistent_state, commit_ingest_failure
from app.modules.chunking.service import chunks_complete
from app.modules.ingestion.index_stages import post_chunk_index_complete
from app.modules.ingestion.publish import (
    blocks_complete,
    finalize_existing_canonical_blocks,
    preview_blocks_published,
    publish_normalized_blocks,
)
from app.modules.ingestion.trace import append_skipped_stage, close_abandoned_running_event, close_stage, open_stage
from app.worker.errors import PermanentWorkerError, TransientWorkerError

logger = logging.getLogger(__name__)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _fail_stage(event: TraceEvent, *, code: str, message: str, started: float) -> None:
    close_stage(event, status="failed", error_code=code, error_detail=message)
    event.duration_ms = _elapsed_ms(started)


def _serving_run_inactive(db: Session, document: Document) -> bool:
    if document.current_version_id is None:
        return False
    run = db.scalar(
        select(ProcessingRun).where(
            ProcessingRun.document_id == document.id,
            ProcessingRun.document_version_id == document.current_version_id,
            ProcessingRun.is_active.is_(True),
        )
    )
    return run is None


def handle_ingest_document(db: Session, job: Job) -> None:
    run_id = uuid.UUID(str(job.payload["processing_run_id"]))
    version_id = uuid.UUID(str(job.payload["document_version_id"]))
    run = db.get(ProcessingRun, run_id)
    version = db.get(DocumentVersion, version_id)
    if run is None or version is None:
        raise PermanentWorkerError("not_found", "Processing run or version not found.")
    document = db.get(Document, run.document_id)
    trace = db.get(QueryTrace, run.trace_id) if run.trace_id else None
    if document is None or trace is None:
        raise PermanentWorkerError("not_found", "Ingest context is incomplete.")

    if close_abandoned_running_event(db, trace_id=trace.id, error_code="worker_lost"):
        db.commit()

    document = db.scalar(select(Document).where(Document.id == document.id).with_for_update())
    if document is None or document.deleted_at is not None:
        raise PermanentWorkerError("document_deleted", "Document was deleted.")

    if document.pending_run_id is None and blocks_complete(db, version.id):
        if (
            run.status == "extracted"
            and run.finished_at is not None
            and chunks_complete(db, run.id)
            and post_chunk_index_complete(db, run.id)
        ):
            blocks_present_skip = db.scalar(
                select(TraceEvent).where(
                    TraceEvent.trace_id == trace.id,
                    TraceEvent.stage == "extract",
                    TraceEvent.status == "skipped",
                    TraceEvent.skip_reason == "blocks_present",
                )
            )
            if blocks_present_skip is not None:
                return
        if preview_blocks_published(db, document=document, version_id=version.id):
            append_skipped_stage(
                db,
                trace_id=trace.id,
                stage="extract",
                skip_reason="blocks_present",
                params={"source_type": document.source_type},
            )
            db.commit()
            return

    if document.pending_run_id == run.id and blocks_complete(db, version.id):
        outcome = finalize_existing_canonical_blocks(
            db,
            document=document,
            run=run,
            version=version,
            trace=trace,
            record_early_skipped_stages=True,
        )
        if outcome == "stale":
            raise PermanentWorkerError("not_found", "This run is no longer the pending run.")
        if outcome == "replay":
            return
        return

    if document.pending_run_id not in (None, run.id):
        raise PermanentWorkerError("not_found", "This run is no longer the pending run.")

    kb = db.get(KnowledgeBase, document.knowledge_base_id)
    if kb is None or kb.deleted_at is not None:
        raise PermanentWorkerError("knowledge_base_deleted", "Knowledge base is not available.")

    if _serving_run_inactive(db, document):
        commit_inconsistent_state(db, document=document, run=run, job=job, trace=trace)
        return

    if (
        blocks_complete(db, version.id)
        and chunks_complete(db, run.id)
        and not post_chunk_index_complete(db, run.id)
        and document.pending_run_id in (None, run.id)
    ):
        outcome = finalize_existing_canonical_blocks(
            db,
            document=document,
            run=run,
            version=version,
            trace=trace,
            record_early_skipped_stages=False,
        )
        if outcome == "stale":
            raise PermanentWorkerError("not_found", "This run is no longer the pending run.")
        if outcome in ("finalized", "replay"):
            return

    if version.storage_state != "stored":
        raise TransientWorkerError("storage_unavailable", "Stored bytes are not ready yet.")

    run.status = "extracting"
    db.commit()

    extract_event = open_stage(db, trace_id=trace.id, stage="extract", params={"source_type": document.source_type})
    db.commit()
    started = time.perf_counter()
    if document.source_type == "web":
        from app.modules.ingestion.integrations.web import crawl_and_extract

        seed_url = document.source_uri or str(job.payload.get("source_url", ""))
        try:
            result = crawl_and_extract(
                seed_url=seed_url,
                max_pages=int(job.payload.get("max_pages", 20)),
                depth=int(job.payload.get("depth", 1)),
            )
            raw_blocks = result.blocks
        except ExtractionError as exc:
            _fail_stage(extract_event, code=exc.code, message=exc.message, started=started)
            if exc.code == "fetch_failed":
                raise TransientWorkerError(exc.code, exc.message) from exc
            raise PermanentWorkerError(exc.code, exc.message) from exc
        extract_ms = _elapsed_ms(started)
        output = {"raw_blocks": len(raw_blocks), **result.trace_output}
        close_stage(extract_event, status="succeeded", output=output)
    else:
        try:
            data = blob_storage.read_bytes(version.storage_path)
        except FileNotFoundError as exc:
            raise PermanentWorkerError("storage_missing", "Stored file bytes are missing.", str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise TransientWorkerError("storage_unavailable", "Stored bytes could not be read.", str(exc)) from exc

        try:
            raw_blocks = extract_raw_blocks(document.source_type, data, filename=version.original_filename)
        except ExtractionError as exc:
            _fail_stage(extract_event, code=exc.code, message=exc.message, started=started)
            raise PermanentWorkerError(exc.code, exc.message) from exc
        extract_ms = _elapsed_ms(started)
        close_stage(extract_event, status="succeeded", output={"raw_blocks": len(raw_blocks)})
    extract_event.duration_ms = extract_ms
    db.commit()

    db.expire_all()
    if blocks_complete(db, version.id):
        run.status = "normalizing"
        normalize_event = open_stage(
            db, trace_id=trace.id, stage="normalize", params={"source_type": document.source_type}
        )
        db.commit()
        outcome = finalize_existing_canonical_blocks(
            db,
            document=document,
            run=run,
            version=version,
            trace=trace,
            normalize_event=normalize_event,
            record_early_skipped_stages=False,
        )
        if outcome == "replay":
            close_stage(normalize_event, status="skipped", skip_reason="blocks_present")
            db.commit()
            return
        if outcome == "stale":
            raise PermanentWorkerError("not_found", "This run is no longer the pending run.")
        return

    run.status = "normalizing"
    normalize_event = open_stage(db, trace_id=trace.id, stage="normalize", params={"source_type": document.source_type})
    db.commit()

    started = time.perf_counter()
    blocks = normalize_blocks(raw_blocks)
    normalize_ms = _elapsed_ms(started)
    if not blocks:
        _fail_stage(
            normalize_event,
            code="empty_content",
            message="The file has no extractable text.",
            started=started,
        )
        raise PermanentWorkerError("empty_content", "The file has no extractable text.")

    outcome = publish_normalized_blocks(
        db,
        document=document,
        run=run,
        version=version,
        trace=trace,
        normalize_event=normalize_event,
        blocks=blocks,
        normalize_ms=normalize_ms,
    )
    if outcome in ("finalized", "published"):
        return
    if outcome == "replay":
        close_stage(normalize_event, status="skipped", skip_reason="blocks_present")
        db.commit()
        return
    if outcome == "stale":
        raise PermanentWorkerError("not_found", "This run is no longer the pending run.")


def persist_ingest_failure(db: Session, job: Job, *, error_code: str, error_message: str) -> None:
    run_id = uuid.UUID(str(job.payload["processing_run_id"]))
    run = db.get(ProcessingRun, run_id)
    if run is None or run.trace_id is None:
        return
    trace = db.get(QueryTrace, run.trace_id)
    document = db.get(Document, run.document_id)
    if trace is None or document is None:
        return

    event = db.scalar(
        select(TraceEvent)
        .where(TraceEvent.trace_id == trace.id, TraceEvent.status == "running")
        .order_by(TraceEvent.sequence.desc())
    )
    if event is not None:
        close_stage(event, status="failed", error_code=error_code, error_detail=error_message)

    commit_ingest_failure(
        db,
        document=document,
        run=run,
        job=job,
        trace=trace,
        error_code=error_code,
        error_detail=error_message,
    )
