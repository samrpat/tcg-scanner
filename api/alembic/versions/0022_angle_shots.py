"""Angled shots, for holo cards.

A holo's whole appeal is that it moves. The rectified, evenly-lit photograph that makes every
other image in this system measurable is precisely the one that hides it: flat on, a holo looks
like a matte card, and a buyer deciding between two listings picks the one where they can see
the foil. Sellers shoot these by hand for exactly that reason.

So a card can carry up to three extra shots taken at an angle. They are never dewarped — the
angle is the content, and rectifying one would be undoing the only thing it is for.

Three slots rather than an open-ended table: one shows the holo, two shows two tilts, and past
that a buyer is scrolling rather than looking. Fixed slots also keep them addressable by a
stable filename, which matters because these go into a listing where a picture URL cannot
change meaning.

Revision ID: 0022
Revises: 0021
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_VALUES = ("angle_1", "angle_2", "angle_3")


def upgrade() -> None:
    for value in _VALUES:
        op.execute(f"ALTER TYPE image_kind ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum, and rebuilding the type to undo an addition
    # nothing depends on is not worth rewriting every row for. Same reasoning as 0016.
    pass
