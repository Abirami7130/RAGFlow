"""Chunk persistence, idempotency, and read APIs."""

from __future__ import annotations

import base64
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domain.configs import DEFAULT_CHUNK_CONFIG
from app.models import CanonicalBlock, Chunk, Document, KnowledgeBaseMember, ProcessingRun
from app.modules.chunking.structure import ChunkDraft, _BlockInput, build_structure_aware_chunks
from app.modules.chunking.tokenizer import token_count
from app.schemas import ChunkRead


def _strategy_params(strategy: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_CHUNK_CONFIG, **(strategy or {})}
    if merged.get("strategy") != "structure_aware":
        raise ValueError("Only the structure_aware chunk strategy is supported.")
    return merged


def build_chunks_for_blocks(
    blocks: list[CanonicalBlock],
    *,
    strategy: dict[str, Any],
) -> list[ChunkDraft]:
    params = _strategy_params(strategy)
    inputs = [
        _BlockInput(block_type=block.block_type, text=block.text, locator=dict(block.locator or {}))
        for block in blocks
    ]
    return build_structure_aware_chunks(
        inputs,
        target_tokens=int(params["target_tokens"]),
        overlap_tokens=int(params["overlap_tokens"]),
        split_oversized_blocks=bool(params["split_oversized_blocks"]),
    )


def chunks_complete(db: Session, run_id: uuid.UUID) -> bool:
    sequences = list(
        db.scalars(
            select(Chunk.sequence).where(Chunk.processing_run_id == run_id).order_by(Chunk.sequence)
        ).all()
    )
    if not sequences:
        return False
    return sequences == list(range(len(sequences)))


def persist_chunks(
    db: Session,
    *,
    run: ProcessingRun,
    version_id: uuid.UUID,
    document_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    blocks: list[CanonicalBlock],
    drafts: list[ChunkDraft],
) -> None:
    for draft in drafts:
        start_block = blocks[draft.block_start_index]
        end_block = blocks[draft.block_end_index]
        db.add(
            Chunk(
                processing_run_id=run.id,
                document_version_id=version_id,
                document_id=document_id,
                knowledge_base_id=knowledge_base_id,
                sequence=draft.sequence,
                text=draft.text,
                token_count=draft.token_count,
                block_start_id=start_block.id,
                block_end_id=end_block.id,
                locator_start=draft.locator_start,
                locator_end=draft.locator_end,
                chunk_metadata=draft.metadata,
            )
        )
    run.chunk_count = len(drafts)
    db.flush()


def replace_chunks_for_run(
    db: Session,
    *,
    run: ProcessingRun,
    version_id: uuid.UUID,
    document_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    blocks: list[CanonicalBlock],
    strategy: dict[str, Any],
) -> list[ChunkDraft]:
    db.execute(delete(Chunk).where(Chunk.processing_run_id == run.id))
    drafts = build_chunks_for_blocks(blocks, strategy=strategy)
    if blocks and not drafts:
        raise ValueError("Chunking produced no output for a non-empty block set.")
    persist_chunks(
        db,
        run=run,
        version_id=version_id,
        document_id=document_id,
        knowledge_base_id=knowledge_base_id,
        blocks=blocks,
        drafts=drafts,
    )
    return drafts


def _require_run_member(db: Session, *, run: ProcessingRun, user_id: uuid.UUID) -> None:
    member = db.get(KnowledgeBaseMember, (run.knowledge_base_id, user_id))
    if member is None:
        raise APIError(404, "not_found", "Processing run not found.")


def list_run_chunks(
    db: Session,
    *,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    cursor: str | None,
) -> tuple[list[ChunkRead], str | None]:
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise APIError(404, "not_found", "Processing run not found.")
    _require_run_member(db, run=run, user_id=user_id)

    after_sequence = _decode_chunk_cursor(cursor) if cursor else None
    query = select(Chunk).where(Chunk.processing_run_id == run.id)
    if after_sequence is not None:
        query = query.where(Chunk.sequence > after_sequence)
    rows = list(db.scalars(query.order_by(Chunk.sequence).limit(limit + 1)).all())
    page = rows[:limit]
    next_cursor = _encode_chunk_cursor(page[-1].sequence) if len(rows) > limit and page else None
    return [_to_chunk_read(row) for row in page], next_cursor


def read_chunk(db: Session, *, chunk_id: uuid.UUID, user_id: uuid.UUID) -> ChunkRead:
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        raise APIError(404, "not_found", "Chunk not found.")
    run = db.get(ProcessingRun, chunk.processing_run_id)
    if run is None:
        raise APIError(404, "not_found", "Chunk not found.")
    _require_run_member(db, run=run, user_id=user_id)
    return _to_chunk_read(chunk)


def _to_chunk_read(chunk: Chunk) -> ChunkRead:
    return ChunkRead(
        id=chunk.id,
        sequence=chunk.sequence,
        text=chunk.text,
        token_count=chunk.token_count,
        block_start_id=chunk.block_start_id,
        block_end_id=chunk.block_end_id,
        locator_start=chunk.locator_start,
        locator_end=chunk.locator_end,
        metadata=chunk.chunk_metadata or {},
        embedding=None,
    )


def _encode_chunk_cursor(sequence: int) -> str:
    return base64.urlsafe_b64encode(str(sequence).encode()).decode()


def _decode_chunk_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        sequence = int(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise APIError(400, "validation_error", "Cursor is invalid.") from exc
    if sequence < 0:
        raise APIError(400, "validation_error", "Cursor is invalid.")
    return sequence


def processing_run_for_version(db: Session, version_id: uuid.UUID | None) -> ProcessingRun | None:
    if version_id is None:
        return None
    return db.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.document_version_id == version_id,
            ProcessingRun.status.in_(("extracted", "ready")),
        )
        .order_by(ProcessingRun.created_at.desc())
    )


def chunk_count_for_version(db: Session, version_id: uuid.UUID | None) -> int:
    run = processing_run_for_version(db, version_id)
    return run.chunk_count if run else 0