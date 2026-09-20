"""The header row of eBay's own upload template.

eBay's Seller Hub Reports hands you a template to fill in, and the columns it contains are the
columns it will accept — they vary by template type ("Create new drafts" against "Create or
schedule new listings") and by category. Generating a file from documented File Exchange
conventions and hoping the headers line up is a guess, and a guess that fails at the point of
upload, after the work is done.

So the operator's own downloaded template is stored, and the export emits exactly its columns in
exactly its order. Values this system knows are mapped in; columns it does not recognise are left
empty for a person to fill; and anything it knows but the template has no column for is reported
rather than silently dropped.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ebay_upload_templates",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        # The header row, in order. Order matters: eBay reads by position as well as by name.
        sa.Column("headers", sa.dialects.postgresql.JSONB(), nullable=False),
        # Everything above the header row — eBay puts instructions and an #INFO line there,
        # and dropping them is what makes an otherwise correct file rejected.
        sa.Column("preamble", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("ebay_upload_templates")
