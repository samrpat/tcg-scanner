"""Track what has actually been sent to eBay, and remember edits made at listing time.

Listing ~2000 cards is a queue worked down over many sittings, so the thing that matters most is
knowing where you stopped. `listed_at` is that mark, and nothing else: it is set when the
operator says the listing is up, not when they open the eBay tab, because opening a tab is not
listing and a queue that lies about its own progress is worse than no queue.

`listing_title` and `listing_price` hold overrides made on the eBay screen. The generated title
reproduces eBay's catalogue name (D-111) but the promo set names are inferred, and the price is
worth a second look against the sold listings that are on screen at that moment — which is
exactly when the operator asked to be able to change it. An override is stored rather than
applied to the card because the generated value should keep tracking the catalogue and the
pricing run; this records that a human disagreed, and with what.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inventory_items",
        sa.Column("listed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inventory_items",
        sa.Column("listing_title", sa.String(120), nullable=True),
    )
    op.add_column(
        "inventory_items",
        sa.Column("listing_price", sa.Numeric(10, 2), nullable=True),
    )
    # The eBay queue filters on this constantly and it is overwhelmingly null.
    op.create_index(
        "ix_inventory_items_listed_at", "inventory_items", ["listed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_items_listed_at", table_name="inventory_items")
    op.drop_column("inventory_items", "listing_price")
    op.drop_column("inventory_items", "listing_title")
    op.drop_column("inventory_items", "listed_at")
