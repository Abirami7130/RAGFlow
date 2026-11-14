"""Ingestion embed, vector_index, and lexical_index stages after chunking."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProcessingRun, QueryTrace, TraceEvent, VectorIndex
from app.modules.embeddings.factory import embedding_provider_from_settings
from app.modules.embeddings.protocol import EmbeddingProvider
from app.modules.chunking.service import chunks_complete
from app.modules.ingestion.trace import close_stage, insert_skipped_tail, open_stage
from app.modules.lexical import lexical_index_is_complete, persist_lexical_for_run
from app.modules.vectorstore import (
    build_staged_embedding_artifacts_for_run,
    build_staged_faiss_artifacts_for_run,
)
from app.modules.vectorstore.paths import sidecar_abs_path, staged_faiss_abs_path
from app.modules.vectorstore.sidecar import sidecar_is_complete, staged_vector_ids_for_run
from app.modules.vectorstore.staged_faiss import staged_faiss_is_complete
from app.worker.errors import PermanentWorkerError, TransientWorkerError

_TRANSIENT_INDEX_ERRORS = frozenset({"embed_failed", "vector_index_failed", "lexical_index_failed"})


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _vector_index_for_run(db: Session, run: ProcessingRun) -> VectorIndex | None:
    if run.embedding_model is None or run.embedding_revision is None or run.embedding_dimension is None:
        return None
    return db.scalars(
        select(VectorIndex).where(
            VectorIndex.knowledge_base_id == run.knowledge_base_id,
            VectorIndex.embedding_model == run.embedding_model,
            VectorIndex.embedding_revision == run.embedding_revision,
            VectorIndex.dimension == run.embedding_dimension,
        )
    ).first()


def embed_artifacts_complete(db: Session, *, run_id: uuid.UUID) -> bool:
    run = db.get(ProcessingRun, run_id)
    if run is None:
        return False
    index = _vector_index_for_run(db, run)
    if index is None:
        return False
    try:
        vector_ids = staged_vector_ids_for_run(db, run_id=run_id, index_id=index.id)
    except ValueError:
        return False
    sidecar_path = sidecar_abs_path(
        knowledge_base_id=run.knowledge_base_id,
        index_id=index.id,
        processing_run_id=run_id,
    )
    return sidecar_is_complete(sidecar_path, expected_ids=vector_ids, dimension=index.dimension)


def vector_index_artifacts_complete(db: Session, *, run_id: uuid.UUID) -> bool:
    run = db.get(ProcessingRun, run_id)
    if run is None:
        return False
    index = _vector_index_for_run(db, run)
    if index is None:
        return False
    try:
        vector_ids = staged_vector_ids_for_run(db, run_id=run_id, index_id=index.id)
    except ValueError:
        return False
    staged_path = staged_faiss_abs_path(
        knowledge_base_id=run.knowledge_base_id,
        index_id=index.id,
        processing_run_id=run_id,
    )
    return staged_faiss_is_complete(staged_path, dimension=index.dimension, expected_ids=vector_ids)


def post_chunk_index_complete(db: Session, run_id: uuid.UUID) -> bool:
    if not chunks_complete(db, run_id):
        return False
    return (
        embed_artifacts_complete(db, run_id=run_id)
        and vector_index_artifacts_complete(db, run_id=run_id)
        and lexical_index_is_complete(db, run_id)
    )


def _stage_succeeded(db: Session, *, trace_id: uuid.UUID, stage: str) -> bool:
    event = db.scalar(
        select(TraceEvent)
        .where(TraceEvent.trace_id == trace_id, TraceEvent.stage == stage, TraceEvent.status == "succeeded")
        .order_by(TraceEvent.attempt.desc())
    )
    return event is not None


def _fail_stage(
    db: Session,
    event: TraceEvent,
    *,
    trace: QueryTrace,
    after_stage: str,
    started: float,
    error_code: str,
    message: str,
) -> None:
    close_stage(event, status="failed", error_code=error_code, error_detail=message)
    event.duration_ms = _elapsed_ms(started)
    insert_skipped_tail(db, trace_id=trace.id, after_stage=after_stage, skip_reason="upstream_failed")
    db.commit()
    if error_code in _TRANSIENT_INDEX_ERRORS:
        raise TransientWorkerError(error_code, message)
    raise PermanentWorkerError(error_code, message)


def _run_embed_stage(
    db: Session,
    *,
    run: ProcessingRun,
    trace: QueryTrace,
    provider: EmbeddingProvider,
) -> uuid.UUID:
    if embed_artifacts_complete(db, run_id=run.id):
        if not _stage_succeeded(db, trace_id=trace.id, stage="embed"):
            append = open_stage(db, trace_id=trace.id, stage="embed", params={"provider": provider.model_id})
            close_stage(append, status="skipped", skip_reason="artifacts_present")
        index = _vector_index_for_run(db, run)
        assert index is not None
        return index.id

    run.status = "embedding"
    event = open_stage(db, trace_id=trace.id, stage="embed", params={"provider": provider.model_id})
    started = time.perf_counter()
    try:
        result = build_staged_embedding_artifacts_for_run(db, run_id=run.id, provider=provider)
    except Exception as exc:
        _fail_stage(
            db,
            event,
            trace=trace,
            after_stage="embed",
            started=started,
            error_code="embed_failed",
            message=str(exc),
        )
    close_stage(
        event,
        status="succeeded",
        output={
            "model": provider.model_id,
            "revision": provider.revision,
            "dimension": provider.dimension,
            "chunk_count": len(result.vector_ids),
            "sidecar_path": result.sidecar_path,
        },
    )
    event.duration_ms = _elapsed_ms(started)
    db.flush()
    return result.index_id


def _run_vector_index_stage(
    db: Session,
    *,
    run: ProcessingRun,
    trace: QueryTrace,
    index_id: uuid.UUID,
) -> None:
    if vector_index_artifacts_complete(db, run_id=run.id):
        if not _stage_succeeded(db, trace_id=trace.id, stage="vector_index"):
            event = open_stage(db, trace_id=trace.id, stage="vector_index", params={"index_id": str(index_id)})
            close_stage(event, status="skipped", skip_reason="artifacts_present")
        return

    run.status = "indexing"
    event = open_stage(db, trace_id=trace.id, stage="vector_index", params={"index_id": str(index_id)})
    started = time.perf_counter()
    try:
        result = build_staged_faiss_artifacts_for_run(db, run_id=run.id, index_id=index_id)
    except Exception as exc:
        _fail_stage(
            db,
            event,
            trace=trace,
            after_stage="vector_index",
            started=started,
            error_code="vector_index_failed",
            message=str(exc),
        )
    close_stage(
        event,
        status="succeeded",
        output={
            "index_id": str(index_id),
            "vector_count": len(result.vector_ids),
            "staged_faiss_path": result.staged_faiss_path,
        },
    )
    event.duration_ms = _elapsed_ms(started)
    db.flush()


def _run_lexical_index_stage(db: Session, *, run: ProcessingRun, trace: QueryTrace) -> None:
    if lexical_index_is_complete(db, run.id):
        if not _stage_succeeded(db, trace_id=trace.id, stage="lexical_index"):
            event = open_stage(db, trace_id=trace.id, stage="lexical_index", params={})
            close_stage(event, status="skipped", skip_reason="artifacts_present")
        return

    run.status = "indexing"
    event = open_stage(db, trace_id=trace.id, stage="lexical_index", params={})
    started = time.perf_counter()
    try:
        result = persist_lexical_for_run(db, run.id)
    except Exception as exc:
        _fail_stage(
            db,
            event,
            trace=trace,
            after_stage="lexical_index",
            started=started,
            error_code="lexical_index_failed",
            message=str(exc),
        )
    close_stage(
        event,
        status="succeeded",
        output={
            "chunk_stats_count": result.chunk_stats_count,
            "posting_count": result.posting_count,
            "replaced": result.replaced,
        },
    )
    event.duration_ms = _elapsed_ms(started)
    db.flush()


def complete_post_chunk_index_stages(
    db: Session,
    *,
    run: ProcessingRun,
    trace: QueryTrace,
    provider: EmbeddingProvider | None = None,
) -> None:
    """Run embed, vector_index, and lexical_index in order. Raises on failure with trace tail."""
    embed_provider = provider or embedding_provider_from_settings()
    index_id = _run_embed_stage(db, run=run, trace=trace, provider=embed_provider)
    _run_vector_index_stage(db, run=run, trace=trace, index_id=index_id)
    _run_lexical_index_stage(db, run=run, trace=trace)
    run.status = "extracted"
    db.flush()