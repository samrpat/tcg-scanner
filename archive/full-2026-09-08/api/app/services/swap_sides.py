"""Swapping a card's front and back after a mis-shot.

Easy to do and, before this, tedious to undo: recognition only ever looks at the front, so a card
photographed back-first simply fails to identify and gives no hint why. The fix was to delete the
card and shoot it again, which throws away two good photographs because they arrived in the wrong
order.

Everything derived from the originals is discarded rather than swapped. The processed crops,
listing renders and corner close-ups all carry a side in their identity — corner cuts are taken
from the front specifically — so swapping their labels would leave a card whose "front corners"
are cut from its back. Deleting and re-deriving costs a few seconds of worker time and cannot be
subtly wrong.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ImageKind
from app.logging_setup import get_logger
from app.models import Image, InventoryItem
from app.storage import get_storage, image_path

log = get_logger(__name__)

# Derived from the originals, and wrong the moment the originals change hands.
DERIVED = (
    ImageKind.PROCESSED_FRONT,
    ImageKind.PROCESSED_BACK,
    ImageKind.LISTING_FRONT,
    ImageKind.LISTING_BACK,
    ImageKind.DETAIL_FRONT_TL,
    ImageKind.DETAIL_FRONT_TR,
    ImageKind.DETAIL_FRONT_BL,
    ImageKind.DETAIL_FRONT_BR,
)


class SwapError(RuntimeError):
    """Raised when there is nothing to swap."""


async def swap(session: AsyncSession, item: InventoryItem) -> dict:
    """Exchange this card's two original photographs, and clear what was derived from them.

    Returns the ids of the originals so the caller can re-queue processing after committing —
    the worker reads them from the database, so enqueueing before the commit races it.
    """
    images = {
        image.kind: image
        for image in (
            await session.execute(
                select(Image).where(Image.inventory_item_id == item.id)
            )
        )
        .scalars()
        .all()
    }

    front = images.get(ImageKind.ORIGINAL_FRONT)
    back = images.get(ImageKind.ORIGINAL_BACK)
    if front is None or back is None:
        raise SwapError("this card needs both a front and a back before they can be swapped")

    storage = get_storage()
    # Read both before writing either: the two paths are about to hold each other's bytes.
    front_bytes = storage.get(front.path)
    back_bytes = storage.get(back.path)

    front_path = image_path(item.sku, ImageKind.ORIGINAL_FRONT)
    back_path = image_path(item.sku, ImageKind.ORIGINAL_BACK)
    storage.put(front_path, back_bytes, overwrite=True)
    storage.put(back_path, front_bytes, overwrite=True)

    # The rows keep their kinds; it is their contents that swapped, so the measured metadata
    # follows the bytes.
    for row, other in ((front, back), (back, front)):
        row.sha256, other.sha256 = other.sha256, row.sha256
        row.bytes, other.bytes = other.bytes, row.bytes
        row.width, other.width = other.width, row.width
        row.height, other.height = other.height, row.height
        break  # one pass swaps both sides of the pair

    # Detection results describe the old picture.
    for row in (front, back):
        row.corners = None
        row.detection_confidence = None
        row.detection_method = None
        row.quality = None
        row.quality_verdict = None
        row.px_per_mm = None
        row.error = None

    removed = 0
    for kind in DERIVED:
        stale = images.get(kind)
        if stale is None:
            continue
        try:
            storage.delete(stale.path)
        except Exception as exc:  # noqa: BLE001 - a stuck file must not block the swap
            log.warning("swap.delete_failed", sku=item.sku, kind=kind.value, error=str(exc))
        await session.delete(stale)
        removed += 1

    # A card identified from the wrong side is identified wrongly.
    item.card_id = None
    item.card_variant_id = None

    await session.flush()
    log.info("swap.done", sku=item.sku, derived_removed=removed)
    return {
        "sku": item.sku,
        "derived_removed": removed,
        "originals": [str(front.id), str(back.id)],
    }


def reprocess_ids(result: dict) -> list[uuid.UUID]:
    return [uuid.UUID(i) for i in result["originals"]]
