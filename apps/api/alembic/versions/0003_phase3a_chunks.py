"""Phase 3A chunk storage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase3a_chunks"
down_revision: str | None = "0002_phase2_ingestion_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("processing_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("processing_runs.id"), nullable=False),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "block_start_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "block_end_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("locator_start", postgresql.JSONB(), nullable=False),
        sa.Column("locator_end", postgresql.JSONB(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("processing_run_id", "sequence", name="uq_chunks_run_sequence"),
    )
    op.create_index("ix_chunks_kb_document", "chunks", ["knowledge_base_id", "document_id"])
    op.create_index("ix_chunks_document_version", "chunks", ["document_version_id"])


def downgrade() -> None:
    op.drop_index("ix_chunks_document_version", table_name="chunks")
    op.drop_index("ix_chunks_kb_document", table_name="chunks")
    op.drop_table("chunks")