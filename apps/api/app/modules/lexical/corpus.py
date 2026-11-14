"""Recompute lexical corpus statistics for a selected active chunk set."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import ChunkStats, LexicalCorpusStats, LexicalPosting, LexicalTermDocumentFrequency


@dataclass(frozen=True, slots=True)
class LexicalCorpusSnapshot:
    knowledge_base_id: uuid.UUID
    chunk_count: int
    token_sum: int
    avg_token_length: float
    term_document_frequencies: tuple[tuple[str, int], ...]


def _normalize_active_chunk_ids(active_chunk_ids: Collection[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    return tuple(sorted(set(active_chunk_ids), key=str))


def recompute_lexical_corpus_for_active_chunks(
    db: Session,
    knowledge_base_id: uuid.UUID,
    active_chunk_ids: Collection[uuid.UUID],
) -> LexicalCorpusSnapshot:
    """Persist N, avgdl, and per-term df for the given active chunk selection."""
    active = _normalize_active_chunk_ids(active_chunk_ids)

    if not active:
        snapshot = LexicalCorpusSnapshot(
            knowledge_base_id=knowledge_base_id,
            chunk_count=0,
            token_sum=0,
            avg_token_length=0.0,
            term_document_frequencies=(),
        )
        _write_snapshot(db, snapshot)
        return snapshot

    stats_rows = list(
        db.scalars(select(ChunkStats).where(ChunkStats.chunk_id.in_(active))).all()
    )
    if len(stats_rows) != len(active):
        found = {row.chunk_id for row in stats_rows}
        missing = [chunk_id for chunk_id in active if chunk_id not in found]
        raise ValueError(f"Missing chunk_stats for active chunks: {missing[:3]}")

    token_sum = sum(row.token_count for row in stats_rows)
    chunk_count = len(active)
    avg_token_length = float(token_sum) / float(chunk_count) if chunk_count else 0.0

    df_rows = db.execute(
        select(LexicalPosting.term, func.count())
        .where(
            LexicalPosting.knowledge_base_id == knowledge_base_id,
            LexicalPosting.chunk_id.in_(active),
        )
        .group_by(LexicalPosting.term)
        .order_by(LexicalPosting.term)
    ).all()
    term_document_frequencies = tuple((str(term), int(count)) for term, count in df_rows)

    snapshot = LexicalCorpusSnapshot(
        knowledge_base_id=knowledge_base_id,
        chunk_count=chunk_count,
        token_sum=token_sum,
        avg_token_length=avg_token_length,
        term_document_frequencies=term_document_frequencies,
    )
    _write_snapshot(db, snapshot)
    return snapshot


def _write_snapshot(db: Session, snapshot: LexicalCorpusSnapshot) -> None:
    db.execute(
        delete(LexicalTermDocumentFrequency).where(
            LexicalTermDocumentFrequency.knowledge_base_id == snapshot.knowledge_base_id
        )
    )
    row = db.get(LexicalCorpusStats, snapshot.knowledge_base_id)
    if row is None:
        row = LexicalCorpusStats(
            knowledge_base_id=snapshot.knowledge_base_id,
            chunk_count=snapshot.chunk_count,
            token_sum=snapshot.token_sum,
            avg_token_length=snapshot.avg_token_length,
        )
        db.add(row)
    else:
        row.chunk_count = snapshot.chunk_count
        row.token_sum = snapshot.token_sum
        row.avg_token_length = snapshot.avg_token_length
    for term, document_frequency in snapshot.term_document_frequencies:
        db.add(
            LexicalTermDocumentFrequency(
                knowledge_base_id=snapshot.knowledge_base_id,
                term=term,
                document_frequency=document_frequency,
            )
        )
    db.flush()


def load_lexical_corpus_snapshot(db: Session, knowledge_base_id: uuid.UUID) -> LexicalCorpusSnapshot | None:
    row = db.get(LexicalCorpusStats, knowledge_base_id)
    if row is None:
        return None
    term_rows = list(
        db.scalars(
            select(LexicalTermDocumentFrequency)
            .where(LexicalTermDocumentFrequency.knowledge_base_id == knowledge_base_id)
            .order_by(LexicalTermDocumentFrequency.term)
        ).all()
    )
    return LexicalCorpusSnapshot(
        knowledge_base_id=knowledge_base_id,
        chunk_count=row.chunk_count,
        token_sum=int(row.token_sum),
        avg_token_length=float(row.avg_token_length),
        term_document_frequencies=tuple((item.term, item.document_frequency) for item in term_rows),
    )