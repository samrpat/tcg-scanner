"""Actual eBay sold listings, kept per card and per condition.

The feed price (TCGplayer via TCGdex) is a different market with different economics, and it
runs low against eBay on cheap cards and high on some scarce ones. What decides what a card is
worth here is what one like it actually sold for on eBay, in the condition being sold.

Individual sales are stored rather than a computed average, for two reasons. The obvious one is
that a median can be recomputed and an average cannot be un-averaged. The one that matters more
is that the operator asked to *see* eBay pricing, not to be told a number — three comps with
titles and dates is an answer they can judge, and a single figure with no provenance is the thing
they already distrust.

`condition` is nullable because plenty of sold listings do not state one. Those are kept: they
are still evidence about the card, just weaker, and discarding them would leave some cards with
no comps at all.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ebay_sales",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "card_variant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("card_variants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # The grade parsed from the listing, when it states one. Null means unknown, not "any".
        sa.Column("condition", sa.String(8), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("shipping", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("item_id", sa.String(32), nullable=True),
        sa.Column("item_url", sa.Text(), nullable=True),
        # What was actually searched, so a bad comp can be traced to a bad query.
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    # The read is always "this variant, this condition, recent first".
    op.create_index(
        "ix_ebay_sales_lookup",
        "ebay_sales",
        ["card_variant_id", "condition", "fetched_at"],
    )
    # The same sold listing coming back on a later run must update rather than duplicate,
    # or a popular card slowly acquires a hundred copies of one sale and skews its own median.
    op.create_index(
        "uq_ebay_sales_item",
        "ebay_sales",
        ["card_variant_id", "item_id"],
        unique=True,
        postgresql_where=sa.text("item_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ebay_sales_item", table_name="ebay_sales")
    op.drop_index("ix_ebay_sales_lookup", table_name="ebay_sales")
    op.drop_table("ebay_sales")
