"""Append-only ingestion trace events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import QueryTrace, TraceEvent
from app.modules.ingestion.pipeline_stages import INGESTION_STAGES


def _utcnow() -> datetime:
    return datetime.now(UTC)


def next_sequence(db: Session, trace_id: uuid.UUID) -> int:
    current = db.scalar(select(func.max(TraceEvent.sequence)).where(TraceEvent.trace_id == trace_id))
    return (current or 0) + 1


def stage_attempt(db: Session, trace_id: uuid.UUID, stage: str) -> int:
    current = db.scalar(
        select(func.max(TraceEvent.attempt)).where(
            TraceEvent.trace_id == trace_id,
            TraceEvent.stage == stage,
        )
    )
    return (current or 0) + 1


def open_stage(
    db: Session,
    *,
    trace_id: uuid.UUID,
    stage: str,
    params: dict[str, Any] | None = None,
) -> TraceEvent:
    event = TraceEvent(
        trace_id=trace_id,
        stage=stage,
        sequence=next_sequence(db, trace_id),
        attempt=stage_attempt(db, trace_id, stage),
        status="running",
        params=params or {},
    )
    db.add(event)
    db.flush()
    return event


def close_stage(
    event: TraceEvent,
    *,
    status: str,
    skip_reason: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    output: dict[str, Any] | None = None,
) -> None:
    ended = _utcnow()
    started = event.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    duration_ms = max(0, int((ended - started).total_seconds() * 1000))
    event.status = status
    event.skip_reason = skip_reason
    event.error_code = error_code
    event.error_detail = error_detail
    event.ended_at = ended
    event.duration_ms = 0 if status == "skipped" else duration_ms
    if output is not None:
        event.output = output


def append_skipped_stage(
    db: Session,
    *,
    trace_id: uuid.UUID,
    stage: str,
    skip_reason: str,
    params: dict[str, Any] | None = None,
) -> TraceEvent:
    event = TraceEvent(
        trace_id=trace_id,
        stage=stage,
        sequence=next_sequence(db, trace_id),
        attempt=stage_attempt(db, trace_id, stage),
        status="skipped",
        skip_reason=skip_reason,
        params=params or {},
        ended_at=_utcnow(),
        duration_ms=0,
    )
    db.add(event)
    db.flush()
    return event


def insert_skipped_tail(
    db: Session,
    *,
    trace_id: uuid.UUID,
    after_stage: str,
    skip_reason: str = "upstream_failed",
) -> None:
    if after_stage not in INGESTION_STAGES:
        tail = list(INGESTION_STAGES)
    else:
        idx = INGESTION_STAGES.index(after_stage)
        tail = list(INGESTION_STAGES[idx + 1 :])
    if not tail:
        return
    sequence = next_sequence(db, trace_id)
    for offset, stage in enumerate(tail):
        event = TraceEvent(
            trace_id=trace_id,
            stage=stage,
            sequence=sequence + offset,
            attempt=1,
            status="skipped",
            skip_reason=skip_reason,
            ended_at=_utcnow(),
            duration_ms=0,
        )
        db.add(event)
    db.flush()


def close_abandoned_running_event(
    db: Session,
    *,
    trace_id: uuid.UUID,
    error_code: str,
) -> bool:
    """Close one running event after worker reclaim. Returns True when an event was closed."""
    event = db.scalar(
        select(TraceEvent)
        .where(TraceEvent.trace_id == trace_id, TraceEvent.status == "running")
        .order_by(TraceEvent.sequence.desc())
    )
    if event is None:
        return False
    close_stage(event, status="failed", error_code=error_code, error_detail="Worker lock expired or process lost.")
    insert_skipped_tail(db, trace_id=trace_id, after_stage=event.stage, skip_reason="upstream_failed")
    return True


def ensure_complete_trace_tail(db: Session, trace: QueryTrace) -> None:
    """Insert missing downstream stages as skipped so the trace lists every pipeline stage."""
    existing = set(
        db.scalars(select(TraceEvent.stage).where(TraceEvent.trace_id == trace.id)).all()
    )
    missing = [stage for stage in INGESTION_STAGES if stage not in existing]
    if not missing:
        return
    sequence = next_sequence(db, trace.id)
    for offset, stage in enumerate(missing):
        event = TraceEvent(
            trace_id=trace.id,
            stage=stage,
            sequence=sequence + offset,
            attempt=1,
            status="skipped",
            skip_reason="upstream_failed",
            ended_at=_utcnow(),
            duration_ms=0,
        )
        db.add(event)
    db.flush()


def finalize_trace_header(
    trace: QueryTrace,
    *,
    status: str,
    error_code: str | None = None,
) -> None:
    trace.status = status
    trace.error_code = error_code
    trace.finished_at = _utcnow()
