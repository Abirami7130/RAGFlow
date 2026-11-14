"""Phase 3K materialized document frequencies for corpus recompute."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_phase3k_term_df"
down_revision: str | None = "0006_phase3d_lexical_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lexical_term_document_frequencies",
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("term", sa.Text(), primary_key=True),
        sa.Column("document_frequency", sa.Integer(), nullable=False),
        sa.CheckConstraint("document_frequency > 0", name="ck_lexical_term_document_frequencies_df"),
    )


def downgrade() -> None:
    op.drop_table("lexical_term_document_frequencies")