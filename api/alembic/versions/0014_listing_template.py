"""Remember which past eBay listing a card should be listed "like".

The operator's existing workflow is to find a comparable sold listing and use eBay's
"Sell one like this", which pre-fills the category, the item specifics and the condition. For
trading cards those pre-filled specifics are what makes a listing findable — they feed eBay's
faceted search — and hand-building a listing from scratch reliably produces a worse one.

Automation must not take that away. Storing the item id of a listing that worked means the
operator can jump straight back into that template for the next copy of the same card.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inventory_items",
        sa.Column("listing_template_item_id", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inventory_items", "listing_template_item_id")
