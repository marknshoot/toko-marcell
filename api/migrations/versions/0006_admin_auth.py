"""Enterprise admin authentication: users and rotating refresh tokens.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31
"""

from collections.abc import Sequence

from alembic import op
from argon2 import PasswordHasher

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEMO_ADMIN_USERNAME = "admin"
DEMO_ADMIN_PASSWORD = "marcell-admin-2026"  # noqa: S105


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id            SERIAL PRIMARY KEY,
            username      VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role          VARCHAR(20) NOT NULL DEFAULT 'admin',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_refresh_tokens (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
            token_hash  VARCHAR(64) UNIQUE NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL,
            revoked     BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS admin_refresh_tokens_token_hash_idx "
        "ON admin_refresh_tokens (token_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS admin_refresh_tokens_user_idx "
        "ON admin_refresh_tokens (user_id)"
    )

    # Seed the demo admin.  Hashing at migration time keeps the committed
    # migration free of a stale hash; ON CONFLICT preserves a rotated password.
    password_hash = PasswordHasher().hash(DEMO_ADMIN_PASSWORD)
    op.execute(
        f"""
        INSERT INTO admin_users (username, password_hash, role)
        VALUES ('{DEMO_ADMIN_USERNAME}', '{password_hash}', 'admin')
        ON CONFLICT (username) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS admin_refresh_tokens_user_idx")
    op.execute("DROP INDEX IF EXISTS admin_refresh_tokens_token_hash_idx")
    op.execute("DROP TABLE IF EXISTS admin_refresh_tokens")
    op.execute("DROP TABLE IF EXISTS admin_users")
