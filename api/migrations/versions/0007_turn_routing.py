"""Record how each copilot turn was routed and how many model calls it cost.

``route``         which path produced the reply (``guardrail`` for a canned reply, ``agent``
                  otherwise); NULL for rows written before this revision.
``llm_calls``     model calls made during the turn, including the guardrail classifier and
                  provider-fallback attempts. This is what a free-tier daily quota counts.

Both are nullable, so the migration is metadata-only and safe to run at startup.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE copilot_turns ADD COLUMN IF NOT EXISTS route TEXT")
    op.execute("ALTER TABLE copilot_turns ADD COLUMN IF NOT EXISTS llm_calls SMALLINT")


def downgrade() -> None:
    op.execute("ALTER TABLE copilot_turns DROP COLUMN IF EXISTS llm_calls")
    op.execute("ALTER TABLE copilot_turns DROP COLUMN IF EXISTS route")
