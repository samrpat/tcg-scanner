"""Group cards into a lot that is sold as one item.

Measured across the first thirty cards: essentially every one is *marginal* on eBay — it clears
its fees and postage alone, but only just, and writing a listing for each is hours of work for a
few dollars. A lot changes the arithmetic completely, because the fixed costs that dominate a
cheap single — the per-order fee and the postage — are paid once for the whole bundle instead of
once per card.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lots",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("note", sa.Text()),
        # What the operator intends to ask for the bundle. Null until they decide; the suggested
        # figure is derived from the members and is not stored, so it cannot go stale.
        sa.Column("asking_price", sa.Numeric(12, 2)),
        sa.Column("listed_at", sa.DateTime(timezone=True)),
        sa.Column("sold_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.add_column(
        "inventory_items",
        sa.Column(
            "lot_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("lots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_inventory_lot", "inventory_items", ["lot_id"])


def downgrade() -> None:
    op.drop_index("ix_inventory_lot", table_name="inventory_items")
    op.drop_column("inventory_items", "lot_id")
    op.drop_table("lots")
