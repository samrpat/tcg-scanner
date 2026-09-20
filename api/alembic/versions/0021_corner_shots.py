"""Whether a batch cuts corner close-ups.

The switch existed only in the browser's local storage, which made it a preference about what to
*download* rather than a decision about what to *make*. Nothing ever cut the four corner crops on
its own, so a batch scanned with the switch on still had no corners in it — the operator had to
find the per-card button, and on a pile of a hundred that is a hundred clicks nobody makes.

Putting it on the batch lets the worker read it: a card finishes processing, its batch says it
wants corners, and they are cut there and then. Defaults to true because the corners are the part
of this system no listing tool can do, and a batch that was never asked should get them.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scan_sessions",
        sa.Column(
            "corner_shots",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("scan_sessions", "corner_shots")
