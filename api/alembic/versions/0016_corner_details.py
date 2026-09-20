"""Corner close-ups: four enlarged quarters of the card front.

On a card worth enough that a buyer zooms in before bidding, the corners are what they zoom at.
Corner whitening and edge wear are what separate Near Mint from Lightly Played, and a photograph
of the whole card resolves neither at the size eBay serves it. Four quarter crops do, and they
are the difference between a buyer trusting a stated grade and a buyer opening a return.

They are a separate image kind rather than a re-crop on demand because they are uploaded to eBay
as listing photographs and must be stable: the URL in a live listing cannot change meaning.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_VALUES = (
    "detail_front_tl",
    "detail_front_tr",
    "detail_front_bl",
    "detail_front_br",
)


def upgrade() -> None:
    for value in _VALUES:
        op.execute(f"ALTER TYPE image_kind ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum. Removing these would mean rebuilding the type
    # and rewriting every row that references it, which is not worth it to undo an addition
    # that nothing else depends on.
    pass
