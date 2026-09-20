"""A session that only produces photographs.

eBay's own card uploader identifies cards and builds listings, and does it well enough that
identifying them here again is duplicated work. The one thing it cannot do is render a card:
rectified to its true 88x63mm, squared, with a known margin of real background around it and
close-ups of each corner. That is what this system is uniquely for.

So a session can be marked photos-only. Cards scanned into it are cropped, rendered and cut into
corners as usual, and recognition never runs — which also removes the two manual steps that cost
the most time, picking a variant and confirming an identity.

Reversible on purpose. If the uploader does not work out, clearing the flag and running
`make reconcile` identifies the batch as though it had always been a normal one.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scan_sessions",
        sa.Column(
            "photos_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("scan_sessions", "photos_only")
