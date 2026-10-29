"""Phase 9: copilot turn telemetry and thumbs feedback.

Two append-only tables:

- ``copilot_turns`` — one row per copilot turn (guardrail short-circuit, model
  answer, cart action or error) with latency, tools, token usage and model.
- ``copilot_feedback`` — explicit thumbs up/down votes, linked to a thread.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS copilot_turns (
            id            BIGSERIAL PRIMARY KEY,
            thread_id     TEXT,
            session_id    TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            guardrail     TEXT,
            outcome       TEXT NOT NULL
                          CHECK (outcome IN ('answered', 'guardrail', 'unanswered', 'cart', 'error')),
            tool_names    TEXT[] NOT NULL DEFAULT '{}'::text[],
            latency_ms    REAL,
            input_tokens  INTEGER,
            output_tokens INTEGER,
            model         TEXT,
            cost_usd      NUMERIC
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS copilot_turns_created_idx ON copilot_turns (created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS copilot_turns_thread_idx ON copilot_turns (thread_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS copilot_feedback (
            id         BIGSERIAL PRIMARY KEY,
            thread_id  TEXT NOT NULL,
            session_id TEXT,
            rating     SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS copilot_feedback_created_idx ON copilot_feedback (created_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS copilot_feedback_created_idx")
    op.execute("DROP TABLE IF EXISTS copilot_feedback")

    op.execute("DROP INDEX IF EXISTS copilot_turns_thread_idx")
    op.execute("DROP INDEX IF EXISTS copilot_turns_created_idx")
    op.execute("DROP TABLE IF EXISTS copilot_turns")
