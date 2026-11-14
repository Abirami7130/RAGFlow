"""Phase 3C vector_indexes and vector_mappings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase3c_vector_indexes"
down_revision: str | None = "0004_chunk_block_fk_cascade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vector_indexes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id"),
            nullable=False,
        ),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_revision", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=8), nullable=False),
        sa.Column("faiss_path", sa.Text(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("vector_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_vector_id", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("metric IN ('ip')", name="ck_vector_indexes_metric"),
        sa.CheckConstraint("dimension > 0", name="ck_vector_indexes_dimension"),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "embedding_model",
            "embedding_revision",
            "dimension",
            "metric",
            name="uq_vector_indexes_kb_identity",
        ),
    )
    op.create_table(
        "vector_mappings",
        sa.Column(
            "index_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vector_indexes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("vector_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("l2_norm", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "state IN ('staged', 'committed', 'retired')",
            name="ck_vector_mappings_state",
        ),
        sa.UniqueConstraint("index_id", "chunk_id", name="uq_vector_mappings_index_chunk"),
    )
    op.create_index("ix_vector_mappings_index_state", "vector_mappings", ["index_id", "state"])


def downgrade() -> None:
    op.drop_index("ix_vector_mappings_index_state", table_name="vector_mappings")
    op.drop_table("vector_mappings")
    op.drop_table("vector_indexes")