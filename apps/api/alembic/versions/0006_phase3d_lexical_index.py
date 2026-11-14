"""Phase 3D chunk_stats, lexical_postings, lexical_corpus_stats."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase3d_lexical_index"
down_revision: str | None = "0005_phase3c_vector_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunk_stats",
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id"),
            nullable=False,
        ),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "processing_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("processing_runs.id"),
            nullable=False,
        ),
        sa.CheckConstraint("token_count >= 0", name="ck_chunk_stats_token_count"),
    )
    op.create_table(
        "lexical_postings",
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id"),
            primary_key=True,
        ),
        sa.Column("term", sa.Text(), primary_key=True),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "processing_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("processing_runs.id"),
            nullable=False,
        ),
        sa.Column("term_frequency", sa.Integer(), nullable=False),
        sa.CheckConstraint("term_frequency > 0", name="ck_lexical_postings_term_frequency"),
    )
    op.create_index("ix_lexical_postings_chunk_id", "lexical_postings", ["chunk_id"])
    op.create_index(
        "ix_lexical_postings_processing_run_id",
        "lexical_postings",
        ["processing_run_id"],
    )
    op.create_table(
        "lexical_corpus_stats",
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id"),
            primary_key=True,
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("token_sum", sa.BigInteger(), nullable=False),
        sa.Column("avg_token_length", sa.Float(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("chunk_count >= 0", name="ck_lexical_corpus_stats_chunk_count"),
        sa.CheckConstraint("token_sum >= 0", name="ck_lexical_corpus_stats_token_sum"),
    )


def downgrade() -> None:
    op.drop_table("lexical_corpus_stats")
    op.drop_index("ix_lexical_postings_processing_run_id", table_name="lexical_postings")
    op.drop_index("ix_lexical_postings_chunk_id", table_name="lexical_postings")
    op.drop_table("lexical_postings")
    op.drop_table("chunk_stats")