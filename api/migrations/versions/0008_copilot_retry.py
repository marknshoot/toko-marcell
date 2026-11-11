"""add copilot_turns.is_retry

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-11
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Separate a retried turn from a fresh one in the KPI query: counting an error
    # followed by a successful retry as two ordinary turns halves the measured
    # answer rate for reasons the shopper never sees.
    op.execute("ALTER TABLE copilot_turns ADD COLUMN IF NOT EXISTS is_retry BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.drop_column("copilot_turns", "is_retry")
