"""Which section new scans go into, as an explicit pointer.

It used to be "the last section in the batch's order", which is right the moment you create
one and wrong as soon as you want to go back. The operator finishes the reverse holos, moves
on to normals, and then finds three more reverse holos at the bottom of the box — under the
old rule the only way back was to reorder the sections, which changes how they read on the
screen to express something that is not about order at all.

So where scans land is now its own fact, separate from how the sections are arranged.

SET NULL, because a pointer to a deleted section must become "no section" rather than a
dangling id — and the fallback, the last section in order, is still there for a batch that has
never been pointed anywhere.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scan_sessions",
        sa.Column(
            "active_section_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batch_sections.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("scan_sessions", "active_section_id")
