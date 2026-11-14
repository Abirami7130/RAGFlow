"""Ingestion activate stage: live publish then atomic activation."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.models import Document, ProcessingRun, QueryTrace
from app.modules.activation.readiness import vector_index_id_for_run
from app.modules.activation.service import activate_processing_run
from app.modules.ingestion.trace import close_stage, open_stage
from app.modules.vectorstore import publish_live_faiss_for_run
from app.worker.errors import PermanentWorkerError


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def complete_activate_stage(
    db: Session,
    *,
    document: Document,
    run: ProcessingRun,
    trace: QueryTrace,
) -> None:
    index_id = vector_index_id_for_run(db, run)
    if index_id is None:
        raise PermanentWorkerError("activation_failed", "Embedding index metadata is missing on the run.")

    event = open_stage(db, trace_id=trace.id, stage="activate", params={"processing_run_id": str(run.id)})
    started = time.perf_counter()
    try:
        publish_live_faiss_for_run(db, run_id=run.id, index_id=index_id)
        result = activate_processing_run(db, run_id=run.id)
    except Exception as exc:
        close_stage(event, status="failed", error_code="activation_failed", error_detail=str(exc))
        event.duration_ms = _elapsed_ms(started)
        raise PermanentWorkerError("activation_failed", str(exc)) from exc

    close_stage(
        event,
        status="succeeded",
        output={
            "current_version_id": str(result.version_id),
            "replaced_version_id": str(result.replaced_version_id) if result.replaced_version_id else None,
        },
    )
    event.duration_ms = _elapsed_ms(started)
    run.status = "ready"
    db.flush()