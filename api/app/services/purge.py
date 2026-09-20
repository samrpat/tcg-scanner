"""Removing cards, including the photographs on disk.

Two things here are easy to get wrong and were, first time.

**Let Postgres do the cascade.** Every child table already declares `ON DELETE CASCADE`, but
`session.delete(obj)` makes SQLAlchemy load the children and null their foreign keys first —
which fails outright against a `NOT NULL` column like `condition_assessments.inventory_item_id`.
A bulk `DELETE` statement lets the database do what it already knows how to do.

**Delete the files last.** Removing a directory cannot be rolled back, so it must happen after
the transaction commits, not before. Doing it first means a failed delete leaves cards in the
database whose photographs are gone — which is worse than either outcome on its own, and is
exactly what happened before this was split in two.

The images do have to go. Storage paths are derived from the SKU and SKUs are allocated as
`max(sku) + 1`, so after the last card is deleted the next scan is `CARD-000001` again and would
write into a directory still holding the previous card's photographs. That is not untidiness, it
is a listing with the wrong picture on it.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models import InventoryItem, User
from app.storage import get_storage

log = get_logger(__name__)


async def purge_rows(session: AsyncSession, user: User, skus: list[str]) -> list[str]:
    """Delete the database rows. Returns the SKUs that existed, for the caller to clean up.

    Does not touch the filesystem — see the module docstring. The caller commits, then calls
    `purge_images` with what comes back.
    """
    if not skus:
        return []

    present = list(
        (
            await session.execute(
                select(InventoryItem.sku).where(
                    InventoryItem.user_id == user.id, InventoryItem.sku.in_(skus)
                )
            )
        )
        .scalars()
        .all()
    )
    if not present:
        return []

    await session.execute(
        delete(InventoryItem).where(
            InventoryItem.user_id == user.id, InventoryItem.sku.in_(present)
        )
    )
    return present


def purge_images(skus: list[str]) -> int:
    """Delete the stored photographs for these SKUs. Call only after a successful commit."""
    storage = get_storage()
    removed = 0
    for sku in skus:
        try:
            removed += storage.delete_prefix(sku)
        except Exception as exc:  # noqa: BLE001 - one stuck file must not strand the rest
            log.warning("purge.images_failed", sku=sku, error=str(exc))
    log.info("purge.images", skus=len(skus), files=removed)
    return removed
