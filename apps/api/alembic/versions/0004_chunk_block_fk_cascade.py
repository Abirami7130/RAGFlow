"""Cascade chunk block foreign keys when canonical blocks are removed."""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_chunk_block_fk_cascade"
down_revision: str | None = "0003_phase3a_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("chunks_block_start_id_fkey", "chunks", type_="foreignkey")
    op.drop_constraint("chunks_block_end_id_fkey", "chunks", type_="foreignkey")
    op.create_foreign_key(
        "chunks_block_start_id_fkey",
        "chunks",
        "canonical_blocks",
        ["block_start_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "chunks_block_end_id_fkey",
        "chunks",
        "canonical_blocks",
        ["block_end_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("chunks_block_start_id_fkey", "chunks", type_="foreignkey")
    op.drop_constraint("chunks_block_end_id_fkey", "chunks", type_="foreignkey")
    op.create_foreign_key(
        "chunks_block_start_id_fkey",
        "chunks",
        "canonical_blocks",
        ["block_start_id"],
        ["id"],
    )
    op.create_foreign_key(
        "chunks_block_end_id_fkey",
        "chunks",
        "canonical_blocks",
        ["block_end_id"],
        ["id"],
    )