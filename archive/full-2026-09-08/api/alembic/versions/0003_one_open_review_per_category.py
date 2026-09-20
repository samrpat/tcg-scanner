"""One open review per item per category.

Found by running the pipeline for real: the worker processes a card's two sides in
parallel, and both reconciliations read "no open review" before either inserted one.
The application now locks the item as well, but a partial unique index makes the
duplicate impossible rather than merely unlikely.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-23 21:50:43.211776
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Any duplicates already created by the race would block the index. Keep the newest
    # open review per (item, category) and dismiss the rest.
    op.execute(
        """
        UPDATE reviews SET status = 'dismissed'
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY inventory_item_id, category ORDER BY created_at DESC
                ) AS rn
                FROM reviews WHERE status = 'open'
            ) ranked WHERE rn > 1
        )
        """
    )
    op.create_index(
        "uq_review_one_open_per_category",
        "reviews",
        ["inventory_item_id", "category"],
        unique=True,
        postgresql_where="status = 'open'",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_review_one_open_per_category", table_name="reviews", postgresql_where="status = 'open'"
    )
