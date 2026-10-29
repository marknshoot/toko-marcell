"""Phase 10: log the shopper question behind an honest unanswered turn.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE copilot_turns ADD COLUMN IF NOT EXISTS unanswered_question TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE copilot_turns DROP COLUMN IF EXISTS unanswered_question")
