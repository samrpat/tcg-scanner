"""Mark a card as needing to be photographed again.

A poor capture is not a decision to be made at a desk — it is a card to pick up and re-shoot.
Until now the only responses to an image-quality flag were to accept it or to re-run the crop,
neither of which helps when the photograph itself is the problem. Asking for a rescan needs to
survive until the card is actually back in front of the camera, so it is state on the item.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inventory_items",
        sa.Column("rescan_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_inventory_rescan",
        "inventory_items",
        ["user_id", "rescan_requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_rescan", table_name="inventory_items")
    op.drop_column("inventory_items", "rescan_requested_at")
