"""Phase 2 document ingestion tables."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

SOURCE_TYPES = (
    "pdf",
    "docx",
    "txt",
    "md",
    "pptx",
    "csv",
    "xlsx",
    "json",
    "web",
    "github",
)

DOCUMENT_STATUSES = ("queued", "extracted", "failed", "ready")

RUN_STATUSES = (
    "queued",
    "extracting",
    "normalizing",
    "chunking",
    "embedding",
    "indexing",
    "activating",
    "extracted",
    "ready",
    "failed",
)

BLOCK_TYPES = ("heading", "paragraph", "list_item", "table", "code", "caption")

STORAGE_STATES = ("incoming", "stored")

JOB_STATUSES = ("pending", "running", "succeeded", "failed")

TRACE_KINDS = ("ingestion", "query")

STAGE_STATUSES = ("running", "succeeded", "failed", "skipped")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(f"source_type IN {SOURCE_TYPES}", name="ck_documents_source_type"),
        CheckConstraint(f"status IN {DOCUMENT_STATUSES}", name="ck_documents_status"),
        CheckConstraint(
            "(pending_version_id IS NULL) = (pending_run_id IS NULL)",
            name="ck_documents_pending_pointers",
        ),
        CheckConstraint(
            "status != 'queued' OR (current_version_id IS NULL AND preview_version_id IS NULL)",
            name="ck_documents_status_queued",
        ),
        CheckConstraint(
            "status != 'extracted' OR (preview_version_id IS NOT NULL AND current_version_id IS NULL)",
            name="ck_documents_status_extracted",
        ),
        CheckConstraint(
            "status != 'failed' OR (current_version_id IS NULL AND preview_version_id IS NULL)",
            name="ck_documents_status_failed",
        ),
        CheckConstraint(
            "status != 'ready' OR current_version_id IS NOT NULL",
            name="ck_documents_status_ready",
        ),
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["document_versions.id", "document_versions.document_id"],
            name="fk_documents_current_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["preview_version_id", "id"],
            ["document_versions.id", "document_versions.document_id"],
            name="fk_documents_preview_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["pending_version_id", "id"],
            ["document_versions.id", "document_versions.document_id"],
            name="fk_documents_pending_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_documents_kb_created_active", "knowledge_base_id", text("created_at DESC"), postgresql_where=text("deleted_at IS NULL")),
        Index("ix_documents_kb_status_active", "knowledge_base_id", "status", postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    preview_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pending_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pending_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_runs.id", deferrable=True, initially="DEFERRED"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        foreign_keys="DocumentVersion.document_id",
    )
    processing_runs: Mapped[list["ProcessingRun"]] = relationship(
        back_populates="document",
        foreign_keys="ProcessingRun.document_id",
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_versions_document_number"),
        UniqueConstraint("id", "document_id", name="uq_document_versions_id_document"),
        Index(
            "uq_document_versions_document_content_hash",
            "document_id",
            "content_hash",
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL"),
        ),
        Index(
            "uq_document_versions_kb_dedup_hash",
            "knowledge_base_id",
            "dedup_hash",
            unique=True,
            postgresql_where=text("dedup_hash IS NOT NULL"),
        ),
        Index("ix_document_versions_document", "document_id"),
        CheckConstraint(f"storage_state IN {STORAGE_STATES}", name="ck_document_versions_storage_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedup_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    storage_state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="incoming")
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    mime_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="application/octet-stream")
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document: Mapped[Document] = relationship(
        back_populates="versions",
        foreign_keys=[document_id],
    )
    canonical_blocks: Mapped[list["CanonicalBlock"]] = relationship(back_populates="document_version")


class CanonicalBlock(Base):
    __tablename__ = "canonical_blocks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "sequence", name="uq_canonical_blocks_version_sequence"),
        CheckConstraint(f"block_type IN {BLOCK_TYPES}", name="ck_canonical_blocks_block_type"),
        Index("ix_canonical_blocks_document_version", "document_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict] = mapped_column(JSONB, nullable=False)
    block_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")

    document_version: Mapped["DocumentVersion"] = relationship(back_populates="canonical_blocks")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("processing_run_id", "sequence", name="uq_chunks_run_sequence"),
        Index("ix_chunks_kb_document", "knowledge_base_id", "document_id"),
        Index("ix_chunks_document_version", "document_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    processing_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_runs.id"), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    block_start_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_blocks.id", ondelete="CASCADE"), nullable=False
    )
    block_end_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_blocks.id", ondelete="CASCADE"), nullable=False
    )
    locator_start: Mapped[dict] = mapped_column(JSONB, nullable=False)
    locator_end: Mapped[dict] = mapped_column(JSONB, nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class QueryTrace(Base):
    __tablename__ = "query_traces"
    __table_args__ = (
        CheckConstraint(f"kind IN {TRACE_KINDS}", name="ck_query_traces_kind"),
        Index("ix_query_traces_kb_started", "knowledge_base_id", text("started_at DESC")),
        Index("ix_query_traces_message", "message_id"),
        Index("ix_query_traces_processing_run", "processing_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    processing_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list["TraceEvent"]] = relationship(back_populates="trace")
    processing_runs: Mapped[list["ProcessingRun"]] = relationship(
        back_populates="trace",
        foreign_keys="ProcessingRun.trace_id",
    )


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES}", name="ck_processing_runs_status"),
        CheckConstraint(
            "user_retry_count <= user_retry_limit",
            name="ck_processing_runs_user_retry_limit",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "document_id"],
            ["document_versions.id", "document_versions.document_id"],
            name="fk_processing_runs_version_document",
        ),
        Index(
            "uq_processing_runs_one_active_per_document",
            "document_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_processing_runs_kb_status", "knowledge_base_id", "status"),
        Index("ix_processing_runs_version_created", "document_version_id", text("created_at DESC")),
        Index("ix_processing_runs_document_created", "document_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), nullable=False)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    chunk_strategy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("query_traces.id"), nullable=True)
    user_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    user_retry_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="processing_runs", foreign_keys=[document_id])
    document_version: Mapped[DocumentVersion] = relationship(foreign_keys=[document_version_id])
    trace: Mapped[QueryTrace | None] = relationship(
        back_populates="processing_runs",
        foreign_keys=[trace_id],
    )


class TraceEvent(Base):
    __tablename__ = "trace_events"
    __table_args__ = (
        UniqueConstraint("trace_id", "sequence", name="uq_trace_events_trace_sequence"),
        CheckConstraint(f"status IN {STAGE_STATUSES}", name="ck_trace_events_status"),
        Index("ix_trace_events_trace_stage_attempt", "trace_id", "stage", "attempt"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("query_traces.id", ondelete="CASCADE"), nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    input: Mapped[dict] = mapped_column("input", JSONB, nullable=False, server_default="{}")
    output: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    trace: Mapped[QueryTrace] = relationship(back_populates="events")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(f"status IN {JOB_STATUSES}", name="ck_jobs_status"),
        Index(
            "ix_jobs_pending_run_after",
            "status",
            "run_after",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "uq_jobs_live_idempotency",
            "job_type",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
        Index(
            "ix_jobs_running_locked_at",
            "status",
            "locked_at",
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="900")
    locked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
