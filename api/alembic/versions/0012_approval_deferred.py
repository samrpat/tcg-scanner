"""Let a card be set aside from the approval queue without approving it.

With the separate review tab gone, the approval queue is the only place cards are worked. An
operator who cannot decide on a card — the variant is ambiguous, the light is wrong, they want a
second look — must be able to move past it, or the queue stops at the first hard card and the
rest of the collection waits behind it.

Distinct from `rescan_requested_at`, which means "this needs photographing again". This means
"not now".

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inventory_items",
        sa.Column("approval_deferred_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inventory_items", "approval_deferred_at")
