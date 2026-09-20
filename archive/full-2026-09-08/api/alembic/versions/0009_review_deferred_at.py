"""Give a review a "later" state that can be returned to.

Skipping a review used to dismiss it permanently: the only way to say "not now" was to say
"never". Deferring is a different thing and needs its own column — the review stays open and
still counts as outstanding work, it simply moves out of the main queue until asked for.

A nullable timestamp rather than a new enum value, so an existing `open` review is unchanged and
"deferred" is expressible as a fact about when, not a separate status to keep consistent.

Revision ID: 0009_review_deferred_at
Revises: 0008_art_grid_hash
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("deferred_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The queue asks for "open and not deferred" on every poll, so index the pair.
    op.create_index(
        "ix_reviews_deferred",
        "reviews",
        ["user_id", "status", "deferred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reviews_deferred", table_name="reviews")
    op.drop_column("reviews", "deferred_at")
