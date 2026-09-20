"""Every card is confirmed by a person before it counts as done.

Confidence thresholds decide what to *ask about*, not what is true. A card identified at 1.000
can still be the wrong card — reprints share art, foil is unreadable from a photograph, and a
crop can be subtly wrong in ways that only matter once a buyer is looking at it. The cost of
being wrong is asymmetric: a few seconds of confirmation against a mis-sold card.

So approval is explicit state, not an inference from confidence.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inventory_items",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The approval queue asks "oldest unapproved for this user" on every card, so index it.
    op.create_index(
        "ix_inventory_approval",
        "inventory_items",
        ["user_id", "approved_at", "sku"],
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_approval", table_name="inventory_items")
    op.drop_column("inventory_items", "approved_at")
