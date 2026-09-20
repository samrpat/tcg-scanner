"""A password on the front door.

Until now this application had no authentication of any kind. Every endpoint was open to
anything that could reach the port — including the ones that delete inventory, and the one that
publishes photographs to the internet. On a home network that is every device on the network;
behind a tunnel it is everybody. It has been the single thing standing between this and being
deployable, and it is fixed here.

Two columns and one table:

- `users.password_hash` — scrypt, from the standard library. No bcrypt or argon2 dependency,
  which on an arm64 Pi image means no compiler and no wheel to go missing. scrypt is a proper
  memory-hard KDF; the parameters are stored alongside the hash so they can be raised later
  without invalidating anybody's password.
- `users.password_set_at` — so "this instance has never been claimed" is a fact in the
  database rather than a guess from a null hash.
- `auth_sessions` — one row per logged-in device, holding a **hash** of the token, never the
  token. Server-side rather than a self-contained signed cookie specifically so that a lost
  phone can be revoked: with a stateless token the only remedy is rotating a secret and logging
  every device out, which is the kind of remedy nobody uses.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column(
        "users", sa.Column("password_set_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "auth_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # sha256 of the token. A stolen database backup must not be a set of working cookies.
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        # Free text, for telling one device from another when revoking. Never trusted.
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Expiry is checked on every authenticated request, so it is worth an index.
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_column("users", "password_set_at")
    op.drop_column("users", "password_hash")
