"""Extra shots — the photographs the standard six do not cover.

Every other image in this system is rectified: flat on, square, exactly 88 x 63 mm, because that
is what makes a card measurable and what makes a batch look deliberate rather than casual.

For some cards that treatment removes the thing worth seeing. It flattens a holo into a matte
card, evens the light across a scuff until it disappears, and squares away the curve of a warped
card. So a card can carry up to three photographs framed by hand — a tilt that catches the foil,
a close-up of a crease, a signature, the edge of something thick — and they are never dewarped.
What the operator chose to show is the content, and straightening it would be undoing it.

They are still normalised, which is a different thing from rectified:

- **Orientation is applied.** A phone writes the rotation into EXIF rather than the pixels, and
  a viewer that ignores the tag shows the card on its side. Baking it in means the file looks
  the same everywhere.
- **EXIF is then discarded.** These are the only images here that go into a listing without
  passing through the pipeline, so they are the only ones that could carry a phone's original
  metadata — including, on most phones, the GPS coordinates of the room it was taken in. That
  must not be uploaded to a public marketplace.
- **The long edge is capped**, and it is re-encoded at the same quality as a listing render, so
  an extra shot and a listing image are the same kind of file.

None of that touches what the photograph shows.
"""

import io
from datetime import datetime

from PIL import Image as PILImage
from PIL import ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CaptureSource, ImageKind
from app.logging_setup import get_logger
from app.models import Image, InventoryItem
from app.storage.paths import EXTRA_KINDS

log = get_logger(__name__)

# Matches the height of a listing render, so the extra shots sit alongside them rather than
# dwarfing them. Well inside eBay's 12 MB per picture.
MAX_EDGE = 3920

# Matches `processed_jpeg_quality` and the corner crops. A holo's appeal is a fine interference
# pattern, which is exactly the kind of detail a lower quality setting turns into mush.
JPEG_QUALITY = 97


class ExtraShotError(Exception):
    """Raised for something the caller can fix, surfaced as a 4xx."""


def normalise(payload: bytes) -> bytes:
    """Orient, strip metadata, cap the size, re-encode. See the module docstring."""
    try:
        with PILImage.open(io.BytesIO(payload)) as opened:
            oriented = ImageOps.exif_transpose(opened)
            # Drop the alpha channel a PNG may carry; JPEG cannot hold one and Pillow raises
            # rather than guessing a background.
            if oriented.mode not in ("RGB", "L"):
                oriented = oriented.convert("RGB")
            oriented.thumbnail((MAX_EDGE, MAX_EDGE), PILImage.LANCZOS)

            buffer = io.BytesIO()
            # `exif` is not carried over, which is the point: a new image is built from the
            # pixels alone, so nothing from the original file's metadata survives.
            oriented.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buffer.getvalue()
    except ExtraShotError:
        raise
    except Exception as exc:  # noqa: BLE001 - a corrupt upload is the caller's problem
        raise ExtraShotError(f"could not read that image: {exc}") from exc


def used_slots(item: InventoryItem) -> list[ImageKind]:
    """Which extra slots this card already has, in order."""
    held = {image.kind for image in item.images}
    return [kind for kind in EXTRA_KINDS if kind in held]


def next_slot(item: InventoryItem) -> ImageKind | None:
    """The first free extra slot, or None when all three are taken."""
    held = {image.kind for image in item.images}
    return next((kind for kind in EXTRA_KINDS if kind not in held), None)


async def attach(
    session: AsyncSession,
    item: InventoryItem,
    payload: bytes,
    *,
    slot: ImageKind | None = None,
    source: CaptureSource = CaptureSource.UPLOAD,
) -> Image:
    """Store one extra shot against this card.

    No processing job is queued, deliberately. Everything else that lands here goes to the
    detector; this must not, and enqueuing it "just in case" would have the worker try to find
    a card's edges in a photograph that was framed to show something other than the card's
    outline.
    """
    from app.storage import get_storage, image_path

    if not payload:
        raise ExtraShotError("empty upload")

    target = slot or next_slot(item)
    if target is None:
        raise ExtraShotError(
            f"{item.sku} already has {len(EXTRA_KINDS)} extra shots — "
            "delete one before adding another"
        )

    encoded = normalise(payload)
    storage = get_storage()
    stored = storage.put(image_path(item.sku, target), encoded, overwrite=True)

    existing = (
        await session.execute(
            select(Image).where(Image.inventory_item_id == item.id, Image.kind == target)
        )
    ).scalar_one_or_none()

    image = existing or Image(inventory_item_id=item.id, kind=target)
    image.path = stored.path
    image.width = stored.width
    image.height = stored.height
    image.bytes = stored.bytes
    image.sha256 = stored.sha256
    image.source = source
    image.error = None
    image.captured_at = datetime.now().astimezone()
    if existing is None:
        session.add(image)
    await session.flush()

    log.info(
        "extra.stored",
        sku=item.sku,
        slot=target.value,
        bytes=stored.bytes,
        size=f"{stored.width}x{stored.height}",
    )
    return image


async def remove(session: AsyncSession, item: InventoryItem, slot: ImageKind) -> bool:
    """Delete one extra shot. Returns whether there was one."""
    from app.storage import get_storage

    image = (
        await session.execute(
            select(Image).where(Image.inventory_item_id == item.id, Image.kind == slot)
        )
    ).scalar_one_or_none()
    if image is None:
        return False

    try:
        get_storage().delete(image.path)
    except Exception as exc:  # noqa: BLE001 - a stuck file must not block the row
        log.warning("extra.delete_failed", path=image.path, error=str(exc))
    await session.delete(image)
    log.info("extra.removed", sku=item.sku, slot=slot.value)
    return True


def slot_from_number(number: int) -> ImageKind:
    if not 1 <= number <= len(EXTRA_KINDS):
        raise ExtraShotError(f"extra must be 1 to {len(EXTRA_KINDS)}")
    return EXTRA_KINDS[number - 1]
