"""Vector index metadata and chunk id mappings (Phase 3C)."""

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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

VECTOR_INDEX_METRICS = ("ip",)

VECTOR_MAPPING_STATES = ("staged", "committed", "retired")


class VectorIndex(Base):
    __tablename__ = "vector_indexes"
    __table_args__ = (
        CheckConstraint(f"metric IN {VECTOR_INDEX_METRICS}", name="ck_vector_indexes_metric"),
        CheckConstraint("dimension > 0", name="ck_vector_indexes_dimension"),
        UniqueConstraint(
            "knowledge_base_id",
            "embedding_model",
            "embedding_revision",
            "dimension",
            "metric",
            name="uq_vector_indexes_kb_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_revision: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    metric: Mapped[str] = mapped_column(String(8), nullable=False)
    faiss_path: Mapped[str] = mapped_column(Text, nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    vector_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_vector_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    mappings: Mapped[list["VectorMapping"]] = relationship(back_populates="index", passive_deletes=True)


class VectorMapping(Base):
    __tablename__ = "vector_mappings"
    __table_args__ = (
        CheckConstraint(f"state IN {VECTOR_MAPPING_STATES}", name="ck_vector_mappings_state"),
        UniqueConstraint("index_id", "chunk_id", name="uq_vector_mappings_index_chunk"),
        Index("ix_vector_mappings_index_state", "index_id", "state"),
    )

    index_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vector_indexes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    vector_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    l2_norm: Mapped[float | None] = mapped_column(Double, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    index: Mapped[VectorIndex] = relationship(back_populates="mappings")