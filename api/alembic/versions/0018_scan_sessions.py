"""Scanning sessions: a batch of cards worked and listed together.

Two thousand cards is not one sitting. Each evening's scanning is a batch that gets approved,
priced and uploaded as a unit, and the question "what am I working on right now" needs an answer
that is not "everything I have ever scanned".

A session is that unit. New captures join the open session; starting a new one closes the
previous, and an old one can be reopened to add to it or look back at what was listed.

`session_id` is nullable and `ON DELETE SET NULL`: deleting a session must never delete
photographs of real cards. Removing the grouping is not the same as removing the cards, and
conflating the two is how someone loses an evening's work by tidying up.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_sessions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Null means open. The newest open session is the one new scans join.
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inventory_items",
        sa.Column(
            "session_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scan_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_inventory_items_session_id", "inventory_items", ["session_id"]
    )
    # "Which session is open" is read on every capture.
    op.create_index(
        "ix_scan_sessions_open",
        "scan_sessions",
        ["user_id", "started_at"],
        postgresql_where=sa.text("closed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_scan_sessions_open", table_name="scan_sessions")
    op.drop_index("ix_inventory_items_session_id", table_name="inventory_items")
    op.drop_column("inventory_items", "session_id")
    op.drop_table("scan_sessions")
