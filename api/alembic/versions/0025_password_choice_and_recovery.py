"""Let the operator choose whether there is a password, and give them a way back in.

Two additions, both about the person installing this rather than the person who wrote it.

**The choice.** Requiring a password is right for almost everybody and wrong for somebody
running this on a wired network in a locked room who does not want to type one a hundred times
a day. That was previously an environment variable — which means a file, a restart, and
knowing the variable exists. It is a question at first run now, and the answer lives here so
the application can honour it without a redeploy.

**The recovery code.** A single-user application with no email has no password reset. The
answer has been "you have shell access, run `make set-password`", which is true and is no use
to somebody running this from a compose file who has forgotten the password on their phone.

A recovery code is shown once at setup and stored hashed, exactly like the password. It is the
only other thing that can set a new one.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deliberately not "auth_enabled": the default for a new column is false, and the safe
    # default here is that a password IS required. Naming it for the unsafe state keeps the
    # default and the safe answer on the same side.
    op.add_column(
        "users",
        sa.Column(
            "auth_disabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("users", sa.Column("recovery_hash", sa.String(255), nullable=True))
    op.add_column(
        "users", sa.Column("recovery_set_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "recovery_set_at")
    op.drop_column("users", "recovery_hash")
    op.drop_column("users", "auth_disabled")
