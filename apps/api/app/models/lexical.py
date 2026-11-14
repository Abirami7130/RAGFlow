"""Lexical index tables and per-chunk BM25 stats (Phase 3D)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ChunkStats(Base):
    __tablename__ = "chunk_stats"
    __table_args__ = (CheckConstraint("token_count >= 0", name="ck_chunk_stats_token_count"),)

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_runs.id"), nullable=False)


class LexicalPosting(Base):
    __tablename__ = "lexical_postings"
    __table_args__ = (
        CheckConstraint("term_frequency > 0", name="ck_lexical_postings_term_frequency"),
        Index("ix_lexical_postings_chunk_id", "chunk_id"),
        Index("ix_lexical_postings_processing_run_id", "processing_run_id"),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id"),
        primary_key=True,
    )
    term: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    processing_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_runs.id"), nullable=False)
    term_frequency: Mapped[int] = mapped_column(Integer, nullable=False)


class LexicalTermDocumentFrequency(Base):
    """Materialized df(t) for the active corpus snapshot (recomputed on activation)."""

    __tablename__ = "lexical_term_document_frequencies"
    __table_args__ = (
        CheckConstraint("document_frequency > 0", name="ck_lexical_term_document_frequencies_df"),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    term: Mapped[str] = mapped_column(Text, primary_key=True)
    document_frequency: Mapped[int] = mapped_column(Integer, nullable=False)


class LexicalCorpusStats(Base):
    __tablename__ = "lexical_corpus_stats"
    __table_args__ = (
        CheckConstraint("chunk_count >= 0", name="ck_lexical_corpus_stats_chunk_count"),
        CheckConstraint("token_sum >= 0", name="ck_lexical_corpus_stats_token_sum"),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id"),
        primary_key=True,
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_sum: Mapped[int] = mapped_column(BigInteger, nullable=False)
    avg_token_length: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )