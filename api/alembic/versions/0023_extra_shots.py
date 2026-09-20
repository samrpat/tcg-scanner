"""Angled shots become extra shots.

They were built for one job — tilting a holo so the foil reads — and that turned out to be too
narrow a name for what the slot is good for. The same three slots hold a close-up of a crease, a
shot of a signature, the edge of a thick card, whatever a particular card needs and the standard
six photographs do not show. Naming them after the first use narrowed what anyone would think to
put in them.

Nothing about the images changes: still up to three, still never rectified, still the only ones
stripped of their metadata on the way in. Only the name.

Files are renamed before the rows, and both halves skip what is already done, so a failed run can
simply be run again.

Revision ID: 0023
Revises: 0022
"""

from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_PAIRS = (("angle_1", "extra_1"), ("angle_2", "extra_2"), ("angle_3", "extra_3"))


def _move_files(forward: bool = True) -> None:
    """Rename the JPEGs to match.

    A migration touching the filesystem is unusual and deliberate here: the path is a *column*,
    so rewriting it without moving the file leaves every row pointing at nothing. Doing it in
    one place means nobody has to remember a second step.

    Local storage only. An object store has no directories to walk and no cheap rename; it also
    is not what this runs on. Missing files are skipped rather than raised — a row whose file is
    already gone is not made worse by this.
    """
    from app.config import settings

    if settings.storage_backend.lower() != "local":
        return
    root = Path(settings.storage_local_root)
    if not root.is_dir():
        return
    for old, new in _PAIRS:
        old_name = old.replace("_", "-") + ".jpg"
        new_name = new.replace("_", "-") + ".jpg"
        if not forward:
            old_name, new_name = new_name, old_name
        for source in root.glob(f"*/{old_name}"):
            target = source.with_name(new_name)
            if target.exists():
                continue
            source.rename(target)


def upgrade() -> None:
    _move_files(forward=True)
    for old, new in _PAIRS:
        # Renaming the enum value carries every row that holds it; no row rewrite needed.
        op.execute(f"ALTER TYPE image_kind RENAME VALUE '{old}' TO '{new}'")
    op.execute(
        sa.text(
            "UPDATE images SET path = replace(path, 'angle-', 'extra-') "
            "WHERE path LIKE '%/angle-_.jpg'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE images SET path = replace(path, 'extra-', 'angle-') "
            "WHERE path LIKE '%/extra-_.jpg'"
        )
    )
    for old, new in _PAIRS:
        op.execute(f"ALTER TYPE image_kind RENAME VALUE '{new}' TO '{old}'")
    _move_files(forward=False)
