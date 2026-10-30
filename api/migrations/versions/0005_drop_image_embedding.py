"""Drop products image_embedding and retire CLIP visual search.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS products_image_embedding_idx")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS image_embedding")


def downgrade() -> None:
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS image_embedding vector(512)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS products_image_embedding_idx "
        "ON products USING hnsw (image_embedding vector_cosine_ops)"
    )
