"""Listing image kinds.

Presentation copies for marketplaces are a separate artefact from the processed images. The
processed image is exactly 88 x 63 mm because every condition measurement divides by that
scale; a listing photograph needs a margin around the card so a buyer can see where it ends
and judge its edges. Keeping them distinct means neither compromises the other.

Adding a value to an existing Postgres enum needs ALTER TYPE, which autogenerate does not
produce — and which cannot run inside a transaction block on older servers, hence the explicit
commit.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_VALUES = ("listing_front", "listing_back")


def upgrade() -> None:
    for value in NEW_VALUES:
        op.execute(f"ALTER TYPE image_kind ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum. Rebuilding the type would mean rewriting every
    # dependent column, and the rows using these values would have nowhere to go — so this is
    # deliberately one-way. Removing the values means restoring from a backup.
    pass
