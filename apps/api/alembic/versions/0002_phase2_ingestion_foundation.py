"""Phase 2 ingestion foundation: documents, versions, blocks, runs, traces, jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase2_ingestion_foundation"
down_revision: str | None = "0001_phase1_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_TYPES = ("pdf", "docx", "txt", "md", "pptx", "csv", "xlsx", "json", "web", "github")
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


def _in_clause(values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"({quoted})"


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preview_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pending_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pending_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"source_type IN {_in_clause(SOURCE_TYPES)}", name="ck_documents_source_type"),
        sa.CheckConstraint(f"status IN {_in_clause(DOCUMENT_STATUSES)}", name="ck_documents_status"),
        sa.CheckConstraint(
            "(pending_version_id IS NULL) = (pending_run_id IS NULL)",
            name="ck_documents_pending_pointers",
        ),
        sa.CheckConstraint(
            "status != 'queued' OR (current_version_id IS NULL AND preview_version_id IS NULL)",
            name="ck_documents_status_queued",
        ),
        sa.CheckConstraint(
            "status != 'extracted' OR (preview_version_id IS NOT NULL AND current_version_id IS NULL)",
            name="ck_documents_status_extracted",
        ),
        sa.CheckConstraint(
            "status != 'failed' OR (current_version_id IS NULL AND preview_version_id IS NULL)",
            name="ck_documents_status_failed",
        ),
        sa.CheckConstraint(
            "status != 'ready' OR current_version_id IS NOT NULL",
            name="ck_documents_status_ready",
        ),
    )
    op.create_index(
        "ix_documents_kb_created_active",
        "documents",
        ["knowledge_base_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_documents_kb_status_active",
        "documents",
        ["knowledge_base_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("dedup_hash", sa.Text(), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("storage_state", sa.String(length=16), nullable=False, server_default="incoming"),
        sa.Column("byte_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("mime_type", sa.Text(), nullable=False, server_default="application/octet-stream"),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_versions_document_number"),
        sa.UniqueConstraint("id", "document_id", name="uq_document_versions_id_document"),
        sa.CheckConstraint(f"storage_state IN {_in_clause(STORAGE_STATES)}", name="ck_document_versions_storage_state"),
    )
    op.create_index("ix_document_versions_document", "document_versions", ["document_id"])
    op.create_index(
        "uq_document_versions_document_content_hash",
        "document_versions",
        ["document_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("content_hash IS NOT NULL"),
    )
    op.create_index(
        "uq_document_versions_kb_dedup_hash",
        "document_versions",
        ["knowledge_base_id", "dedup_hash"],
        unique=True,
        postgresql_where=sa.text("dedup_hash IS NOT NULL"),
    )

    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["current_version_id", "id"],
        ["id", "document_id"],
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_documents_preview_version",
        "documents",
        "document_versions",
        ["preview_version_id", "id"],
        ["id", "document_id"],
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_documents_pending_version",
        "documents",
        "document_versions",
        ["pending_version_id", "id"],
        ["id", "document_id"],
        initially="DEFERRED",
        deferrable=True,
    )

    op.create_table(
        "query_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("processing_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"kind IN {_in_clause(TRACE_KINDS)}", name="ck_query_traces_kind"),
    )
    op.create_index(
        "ix_query_traces_kb_started",
        "query_traces",
        ["knowledge_base_id", sa.text("started_at DESC")],
    )
    op.create_index("ix_query_traces_message", "query_traces", ["message_id"])
    op.create_index("ix_query_traces_processing_run", "query_traces", ["processing_run_id"])

    op.create_table(
        "processing_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("document_versions.id"), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("chunk_strategy", postgresql.JSONB(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_revision", sa.Text(), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("query_traces.id"), nullable=True),
        sa.Column("user_retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_retry_limit", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN {_in_clause(RUN_STATUSES)}", name="ck_processing_runs_status"),
        sa.CheckConstraint("user_retry_count <= user_retry_limit", name="ck_processing_runs_user_retry_limit"),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id"],
            ["document_versions.id", "document_versions.document_id"],
            name="fk_processing_runs_version_document",
        ),
    )
    op.create_index(
        "uq_processing_runs_one_active_per_document",
        "processing_runs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_processing_runs_kb_status", "processing_runs", ["knowledge_base_id", "status"])
    op.create_index(
        "ix_processing_runs_version_created",
        "processing_runs",
        ["document_version_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_processing_runs_document_created",
        "processing_runs",
        ["document_id", sa.text("created_at DESC")],
    )

    op.create_foreign_key(
        "fk_documents_pending_run",
        "documents",
        "processing_runs",
        ["pending_run_id"],
        ["id"],
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_foreign_key(
        "fk_query_traces_processing_run",
        "query_traces",
        "processing_runs",
        ["processing_run_id"],
        ["id"],
    )

    op.create_table(
        "canonical_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("document_version_id", "sequence", name="uq_canonical_blocks_version_sequence"),
        sa.CheckConstraint(f"block_type IN {_in_clause(BLOCK_TYPES)}", name="ck_canonical_blocks_block_type"),
    )
    op.create_index("ix_canonical_blocks_document_version", "canonical_blocks", ["document_version_id"])

    op.create_table(
        "trace_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "trace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("query_traces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("input", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.UniqueConstraint("trace_id", "sequence", name="uq_trace_events_trace_sequence"),
        sa.CheckConstraint(f"status IN {_in_clause(STAGE_STATUSES)}", name="ck_trace_events_status"),
    )
    op.create_index("ix_trace_events_trace_stage_attempt", "trace_events", ["trace_id", "stage", "attempt"])

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id"), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("run_after", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_timeout_seconds", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN {_in_clause(JOB_STATUSES)}", name="ck_jobs_status"),
    )
    op.create_index(
        "ix_jobs_pending_run_after",
        "jobs",
        ["status", "run_after"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "uq_jobs_live_idempotency",
        "jobs",
        ["job_type", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_index(
        "ix_jobs_running_locked_at",
        "jobs",
        ["status", "locked_at"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_running_locked_at", table_name="jobs")
    op.drop_index("uq_jobs_live_idempotency", table_name="jobs")
    op.drop_index("ix_jobs_pending_run_after", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_trace_events_trace_stage_attempt", table_name="trace_events")
    op.drop_table("trace_events")
    op.drop_index("ix_canonical_blocks_document_version", table_name="canonical_blocks")
    op.drop_table("canonical_blocks")
    op.drop_constraint("fk_query_traces_processing_run", "query_traces", type_="foreignkey")
    op.drop_constraint("fk_documents_pending_run", "documents", type_="foreignkey")
    op.drop_index("ix_processing_runs_document_created", table_name="processing_runs")
    op.drop_index("ix_processing_runs_version_created", table_name="processing_runs")
    op.drop_index("ix_processing_runs_kb_status", table_name="processing_runs")
    op.drop_index("uq_processing_runs_one_active_per_document", table_name="processing_runs")
    op.drop_table("processing_runs")
    op.drop_index("ix_query_traces_processing_run", table_name="query_traces")
    op.drop_index("ix_query_traces_message", table_name="query_traces")
    op.drop_index("ix_query_traces_kb_started", table_name="query_traces")
    op.drop_table("query_traces")
    op.drop_constraint("fk_documents_pending_version", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_preview_version", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_index("uq_document_versions_kb_dedup_hash", table_name="document_versions")
    op.drop_index("uq_document_versions_document_content_hash", table_name="document_versions")
    op.drop_index("ix_document_versions_document", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_documents_kb_status_active", table_name="documents")
    op.drop_index("ix_documents_kb_created_active", table_name="documents")
    op.drop_table("documents")
