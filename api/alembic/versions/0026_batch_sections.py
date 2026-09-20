"""Divisions within a batch.

A batch is an evening's scanning. It is not, it turns out, one thing: the operator sorts the
pile before scanning it — reverse holos, normals, unidentified — and then sorts each of those
by condition. By the time cards reach the camera they are already in five or six piles, and
scanning them into one undivided batch throws that sorting away at exactly the moment it was
most expensive to do.

So a batch can hold sections, and cards land in whichever is current. The physical act is
"start a new pile"; the digital one should be the same.

Flat, not nested. The sort is two levels deep — variant, then condition — but a name carries
that perfectly well ("Reverse holo · NM") and a tree would add a dimension to every screen
that touches this for no gain the operator asked for.

`position` rather than relying on `created_at`: sections get reordered, and a sort key that
means "when it was made" cannot express "this one goes first now".

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batch_sections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scan_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # SET NULL rather than CASCADE, deliberately. Deleting a section is a statement about the
    # grouping, never about the cards — the same reasoning that makes deleting a batch keep
    # its cards by default. Losing an evening's scanning by tidying up a heading would be
    # unforgivable.
    op.add_column(
        "inventory_items",
        sa.Column(
            "section_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batch_sections.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_inventory_items_section_id", "inventory_items", ["section_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_items_section_id", table_name="inventory_items")
    op.drop_column("inventory_items", "section_id")
    op.drop_table("batch_sections")
