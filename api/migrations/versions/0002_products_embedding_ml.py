"""Phase 4a: opt-in multilingual embedding column products.embedding_ml + HNSW index.

Replaces the hand-run ``pipelines/migrations/2026_10_phase4.sql``. Schema only —
the vectors are produced offline and ship in the seed dump. Idempotent so it is a
no-op on databases that already received the old SQL file.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding_ml vector(384)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS products_embedding_ml_idx "
        "ON products USING hnsw (embedding_ml vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS products_embedding_ml_idx")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS embedding_ml")
