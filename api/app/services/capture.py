"""Capture: store an original, pair it with its other side, queue the processing.

The pairing rule is the whole point of this module. The physical loop is place, shoot, flip,
shoot, next — so posting a front starts an item and posting a back finishes the one that is
waiting. No filenames, no manual linking (spec §21).
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CaptureSource, ImageKind, InventoryStatus
from app.logging_setup import get_logger
from app.models import Image, InventoryItem
from app.services.scrub import scrub
from app.services.sku import next_sku
from app.storage import get_storage, image_path

log = get_logger(__name__)

FRONT_KINDS = (ImageKind.ORIGINAL_FRONT, ImageKind.PROCESSED_FRONT)
BACK_KINDS = (ImageKind.ORIGINAL_BACK, ImageKind.PROCESSED_BACK)

_FRONT_HINT = re.compile(r"(^|[^a-z])(front|f|obverse)([^a-z]|$)", re.IGNORECASE)
_BACK_HINT = re.compile(r"(^|[^a-z])(back|b|reverse|rear)([^a-z]|$)", re.IGNORECASE)


class CaptureError(Exception):
    """Raised for conditions the caller can fix, surfaced as a 4xx."""


@dataclass(frozen=True)
class CaptureResult:
    item: InventoryItem
    image: Image
    created_item: bool
    next_side: str | None  # what the operator should shoot next


def side_from_filename(name: str | None) -> str | None:
    """Guess front/back from a filename. Returns None when it is genuinely ambiguous."""
    if not name:
        return None
    stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    front = bool(_FRONT_HINT.search(stem))
    back = bool(_BACK_HINT.search(stem))
    if front == back:  # both or neither — do not guess
        return None
    return "front" if front else "back"


async def find_item_awaiting_back(
    session: AsyncSession, user_id: uuid.UUID
) -> InventoryItem | None:
    """The most recent item with a front and no back.

    A correlated NOT EXISTS rather than loading images: at 2,000 items this stays a single
    indexed lookup on the capture path, where the 36-second budget is spent.
    """
    has_front = (
        select(Image.id)
        .where(Image.inventory_item_id == InventoryItem.id, Image.kind == ImageKind.ORIGINAL_FRONT)
        .exists()
    )
    has_back = (
        select(Image.id)
        .where(Image.inventory_item_id == InventoryItem.id, Image.kind == ImageKind.ORIGINAL_BACK)
        .exists()
    )
    return (
        await session.execute(
            select(InventoryItem)
            .where(InventoryItem.user_id == user_id, has_front, ~has_back)
            .order_by(desc(InventoryItem.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()


async def _existing_image(
    session: AsyncSession, item_id: uuid.UUID, kind: ImageKind
) -> Image | None:
    return (
        await session.execute(
            select(Image).where(Image.inventory_item_id == item_id, Image.kind == kind)
        )
    ).scalar_one_or_none()


async def capture(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    side: str,
    payload: bytes,
    source: CaptureSource = CaptureSource.UPLOAD,
    replace: bool = False,
) -> CaptureResult:
    """Store one captured side and return the item it belongs to.

    Returns as soon as the original is on disk. Detection and dewarp are queued by the caller,
    so the shutter never waits for the pipeline (D-005).
    """
    if side not in ("front", "back"):
        raise CaptureError(f"side must be 'front' or 'back', got {side!r}")
    if not payload:
        raise CaptureError("empty upload")

    kind = ImageKind.ORIGINAL_FRONT if side == "front" else ImageKind.ORIGINAL_BACK
    created_item = False

    if side == "front":
        # Every card belongs to a scanning session. One is created on demand, so someone who
        # never opens the sessions screen still gets a batch rather than a null.
        from app.models import User as _User
        from app.routers.sessions import ensure_open_session

        owner = await session.get(_User, user_id)
        batch = await ensure_open_session(session, owner) if owner else None

        item = InventoryItem(
            user_id=user_id,
            sku=await next_sku(session, user_id),
            status=InventoryStatus.CAPTURED,
            session_id=batch.id if batch else None,
        )
        session.add(item)
        await session.flush()
        created_item = True
    else:
        item = await find_item_awaiting_back(session, user_id)
        if item is None:
            raise CaptureError(
                "no card is waiting for a back — shoot a front first, or post to "
                "/api/capture/{sku}/back to attach it to a specific card"
            )

    result = await attach_image(
        session, item=item, kind=kind, payload=payload, source=source, replace=replace
    )
    return CaptureResult(
        item=item,
        image=result,
        created_item=created_item,
        next_side="back" if side == "front" else "front",
    )


async def attach_image(
    session: AsyncSession,
    *,
    item: InventoryItem,
    kind: ImageKind,
    payload: bytes,
    source: CaptureSource = CaptureSource.UPLOAD,
    replace: bool = False,
) -> Image:
    """Write an original to storage and record it.

    Originals are written once. Re-capturing a side needs `replace=True`, which is a deliberate
    act — an accidental double-post must not quietly overwrite a good photograph (REQ-STO-002).
    """
    existing = await _existing_image(session, item.id, kind)
    if existing and not replace:
        raise CaptureError(
            f"{item.sku} already has a {kind.value} image; pass replace=true to overwrite it"
        )

    # Metadata comes off before anything is written. A capture taken through the app's own
    # camera has none — a canvas carries nothing — but an *upload* is the file as the phone
    # wrote it, and this is the copy the batch export offers under `kind=all` and the public
    # photo host serves by URL. Lossless: the segments come out, the pixels do not change.
    payload = scrub(payload)

    storage = get_storage()
    path = image_path(item.sku, kind)
    stored = storage.put(path, payload, overwrite=True)

    image = existing or Image(inventory_item_id=item.id, kind=kind)
    image.path = stored.path
    image.width = stored.width
    image.height = stored.height
    image.bytes = stored.bytes
    image.sha256 = stored.sha256
    image.source = source
    image.captured_at = datetime.now().astimezone()
    image.error = None
    if existing is None:
        session.add(image)
    await session.flush()

    log.info("capture.stored", sku=item.sku, kind=kind.value, bytes=stored.bytes)
    return image


def pair_batch(filenames: list[str]) -> list[str]:
    """Assign front/back to a batch of uploads.

    Filename hints win when every file has one. Otherwise arrival order alternates, which is
    what a burst from a capture rig looks like. Mixed or partial hints fall back to alternation
    rather than half-trusting the names.
    """
    hinted = [side_from_filename(name) for name in filenames]
    if hinted and all(h is not None for h in hinted):
        return [h for h in hinted if h is not None]
    return ["front" if index % 2 == 0 else "back" for index in range(len(filenames))]
