"""Corner close-ups for cards worth looking at closely.

A buyer deciding on a card worth twenty dollars zooms in, and what they zoom at is the corners:
corner whitening and edge wear are what separate Near Mint from Lightly Played, and they occupy
a few millimetres of a card that eBay serves at a size where a few millimetres is a smudge.

So this cuts the front into its four quarters and stores each one. Each crop keeps the outer
margin the listing render already added, which matters — a corner cut hard to the card's edge
gives a buyer no background to judge the edge against, which is the same reason the listing
render exists at all.

The quarters overlap slightly. Without it, wear sitting exactly on the seam would be halved
across two images and look like nothing in either.

Only the front is cut. Backs wear too, but the back is the same picture on every Pokémon card
ever printed and a buyer inspecting the back is inspecting centering, which the whole-card
listing photograph already shows.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ImageKind
from app.logging_setup import get_logger
from app.models import Image, InventoryItem
from app.storage import get_storage, image_path

log = get_logger(__name__)

# Which quarter each kind holds, as (top, left) fractions.
QUADRANTS: dict[ImageKind, tuple[int, int]] = {
    ImageKind.DETAIL_FRONT_TL: (0, 0),
    ImageKind.DETAIL_FRONT_TR: (0, 1),
    ImageKind.DETAIL_FRONT_BL: (1, 0),
    ImageKind.DETAIL_FRONT_BR: (1, 1),
}

# How much of each dimension one corner crop covers, as a fraction. 0.5 is a clean quadrant;
# smaller closes in on the corner itself, which is what a buyer is looking for on a card whose
# middle is fine. Adjustable per card because the right answer depends on where the wear is.
CORNER_FRACTION = 0.54

# Matches `processed_jpeg_quality`. These are the images a buyer judges the card by, and the
# difference between 94 and 97 is visible exactly where it matters — the fine speckle of edge
# whitening, which 94 smooths into the border.
JPEG_QUALITY = 97


@dataclass
class DetailResult:
    rendered: list[str]
    skipped: list[str]
    reason: str | None = None

    def as_dict(self) -> dict:
        return {"rendered": self.rendered, "skipped": self.skipped, "reason": self.reason}


def crop(
    canvas: np.ndarray, row: int, col: int, fraction: float = CORNER_FRACTION
) -> np.ndarray:
    """One corner of the image, covering `fraction` of each dimension.

    Anchored to the corner rather than centred on the quadrant, so shrinking the fraction closes
    in on the corner itself instead of drifting toward the middle of the card — the corner is
    the whole point, and a crop that wanders off it is worse than a wide one.

    Above 0.5 the four crops overlap, which is deliberate at the default: wear sitting exactly
    on a seam would otherwise be halved across two images and look like nothing in either.
    """
    fraction = min(max(fraction, 0.15), 0.9)
    height, width = canvas.shape[:2]
    cut_h, cut_w = int(height * fraction), int(width * fraction)

    y0 = 0 if row == 0 else height - cut_h
    x0 = 0 if col == 0 else width - cut_w
    return canvas[y0 : y0 + cut_h, x0 : x0 + cut_w]


async def build_for_item(
    session: AsyncSession, item: InventoryItem, fraction: float = CORNER_FRACTION
) -> dict:
    """Cut the four corner close-ups from this card's listing front.

    Falls back to the processed front when no listing render exists, which costs the outer
    margin but is better than refusing. Returns a reason rather than raising when there is
    nothing to cut, because the caller is a button on a screen.
    """
    storage = get_storage()
    images = {
        image.kind: image
        for image in (
            await session.execute(select(Image).where(Image.inventory_item_id == item.id))
        )
        .scalars()
        .all()
    }

    source = images.get(ImageKind.LISTING_FRONT) or images.get(ImageKind.PROCESSED_FRONT)
    if source is None:
        return DetailResult([], [k.value for k in QUADRANTS], "no front image yet").as_dict()

    buffer = np.frombuffer(storage.get(source.path), dtype=np.uint8)
    canvas = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if canvas is None:
        return DetailResult([], [k.value for k in QUADRANTS], "front image unreadable").as_dict()

    rendered: list[str] = []
    skipped: list[str] = []

    for kind, (row, col) in QUADRANTS.items():
        piece = crop(canvas, row, col, fraction)
        ok, encoded = cv2.imencode(".jpg", piece, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            skipped.append(kind.value)
            continue

        stored = storage.put(image_path(item.sku, kind), encoded.tobytes(), overwrite=True)
        record = images.get(kind) or Image(inventory_item_id=item.id, kind=kind)
        record.path = stored.path
        record.width = stored.width
        record.height = stored.height
        record.bytes = stored.bytes
        record.sha256 = stored.sha256
        record.source = source.source
        record.captured_at = source.captured_at
        record.px_per_mm = source.px_per_mm
        record.error = None
        if record.id is None:
            session.add(record)
        rendered.append(kind.value)

    await session.flush()
    log.info("corner_details.built", sku=item.sku, rendered=rendered, skipped=skipped)
    return DetailResult(rendered=rendered, skipped=skipped).as_dict()
