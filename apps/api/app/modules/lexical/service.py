"""Persist chunk_stats and lexical_postings for a processing run."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Chunk, ChunkStats, LexicalPosting, ProcessingRun
from app.modules.chunking.tokenizer import token_count
from app.modules.lexical.postings import ChunkLexicalInput, LexicalPostingDraft, build_lexical_posting_drafts


@dataclass(frozen=True, slots=True)
class LexicalPersistResult:
    processing_run_id: uuid.UUID
    chunk_stats_count: int
    posting_count: int
    replaced: bool


def _run_chunks(db: Session, run_id: uuid.UUID) -> list[Chunk]:
    return list(
        db.scalars(select(Chunk).where(Chunk.processing_run_id == run_id).order_by(Chunk.sequence)).all()
    )


def _sorted_drafts(drafts: tuple[LexicalPostingDraft, ...]) -> tuple[LexicalPostingDraft, ...]:
    return tuple(sorted(drafts, key=lambda row: (str(row.chunk_id), row.term)))


def _chunk_token_counts(chunks: list[Chunk]) -> dict[uuid.UUID, int]:
    return {chunk.id: token_count(chunk.text) for chunk in chunks}


def _lexical_matches_run(
    db: Session,
    *,
    run: ProcessingRun,
    chunks: list[Chunk],
    drafts: tuple[LexicalPostingDraft, ...],
    token_counts: dict[uuid.UUID, int],
) -> bool:
    if not chunks:
        return True
    chunk_ids = [chunk.id for chunk in chunks]
    stats_rows = list(db.scalars(select(ChunkStats).where(ChunkStats.chunk_id.in_(chunk_ids))).all())
    if len(stats_rows) != len(chunks):
        return False
    for row in stats_rows:
        expected = token_counts.get(row.chunk_id)
        if expected is None:
            return False
        if row.token_count != expected or row.processing_run_id != run.id or row.knowledge_base_id != run.knowledge_base_id:
            return False

    ordered_drafts = _sorted_drafts(drafts)
    posting_rows = list(
        db.scalars(
            select(LexicalPosting)
            .where(LexicalPosting.processing_run_id == run.id)
            .order_by(LexicalPosting.chunk_id, LexicalPosting.term)
        ).all()
    )
    if len(posting_rows) != len(ordered_drafts):
        return False
    for row, draft in zip(posting_rows, ordered_drafts, strict=True):
        if (
            row.knowledge_base_id != run.knowledge_base_id
            or row.chunk_id != draft.chunk_id
            or row.term != draft.term
            or row.term_frequency != draft.term_frequency
        ):
            return False
    return True


def _clear_lexical_for_run(db: Session, *, run_id: uuid.UUID, chunk_ids: list[uuid.UUID]) -> None:
    db.execute(delete(LexicalPosting).where(LexicalPosting.processing_run_id == run_id))
    if chunk_ids:
        db.execute(delete(ChunkStats).where(ChunkStats.chunk_id.in_(chunk_ids)))


def lexical_index_is_complete(db: Session, run_id: uuid.UUID) -> bool:
    """Return whether chunk_stats and lexical_postings match the run's chunks."""
    run = db.get(ProcessingRun, run_id)
    if run is None:
        return False
    chunks = _run_chunks(db, run_id)
    inputs = [ChunkLexicalInput(chunk_id=chunk.id, text=chunk.text) for chunk in chunks]
    drafts = build_lexical_posting_drafts(inputs)
    token_counts = _chunk_token_counts(chunks)
    return _lexical_matches_run(
        db,
        run=run,
        chunks=chunks,
        drafts=drafts,
        token_counts=token_counts,
    )


def persist_lexical_for_run(db: Session, run_id: uuid.UUID, *, force: bool = False) -> LexicalPersistResult:
    """Write chunk_stats and lexical_postings for a run. Safe to call on retry."""
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise ValueError(f"Processing run {run_id} was not found.")

    chunks = _run_chunks(db, run_id)
    inputs = [ChunkLexicalInput(chunk_id=chunk.id, text=chunk.text) for chunk in chunks]
    drafts = build_lexical_posting_drafts(inputs)
    token_counts = _chunk_token_counts(chunks)

    already_complete = _lexical_matches_run(
        db,
        run=run,
        chunks=chunks,
        drafts=drafts,
        token_counts=token_counts,
    )
    if not force and already_complete:
        posting_count = db.scalar(
            select(func.count()).select_from(LexicalPosting).where(LexicalPosting.processing_run_id == run_id)
        )
        stats_count = len(chunks)
        return LexicalPersistResult(
            processing_run_id=run_id,
            chunk_stats_count=stats_count,
            posting_count=int(posting_count or 0),
            replaced=False,
        )

    chunk_ids = [chunk.id for chunk in chunks]
    _clear_lexical_for_run(db, run_id=run_id, chunk_ids=chunk_ids)

    for chunk in chunks:
        db.add(
            ChunkStats(
                chunk_id=chunk.id,
                knowledge_base_id=run.knowledge_base_id,
                token_count=token_counts[chunk.id],
                processing_run_id=run.id,
            )
        )

    ordered_drafts = _sorted_drafts(drafts)
    for draft in ordered_drafts:
        db.add(
            LexicalPosting(
                knowledge_base_id=run.knowledge_base_id,
                term=draft.term,
                chunk_id=draft.chunk_id,
                processing_run_id=run.id,
                term_frequency=draft.term_frequency,
            )
        )

    db.flush()
    return LexicalPersistResult(
        processing_run_id=run_id,
        chunk_stats_count=len(chunks),
        posting_count=len(ordered_drafts),
        replaced=True,
    )