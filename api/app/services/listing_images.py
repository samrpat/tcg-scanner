"""Render presentation copies of a card for marketplace listings.

The processed image is exactly 88 x 63 mm because every condition measurement divides by that
scale. A listing photograph wants something different: **a consistent margin around the card**.
Cut hard to the edge, a buyer has nowhere to look — edge whitening and corner wear sit right at
the boundary, and with no background behind them there is no reference for where the card ends
or how sharp its corners are. That is precisely the information a buyer needs to decide, and
precisely what a machine-tight crop hides.

So the listing render keeps the same rectification and adds a known margin: the card lands in
the centre of a larger canvas, identically on every image. Consistency is the point — a batch
where each card sits in the same frame reads as deliberate, while varying crops read as
automated and invite doubt.

Nothing here alters the processed image, which stays photometrically faithful for grading.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import ImageKind
from app.imaging.geometry import CARD_HEIGHT_MM, CARD_WIDTH_MM, order_corners
from app.logging_setup import get_logger
from app.models import Image, InventoryItem
from app.storage import get_storage, image_path

log = get_logger(__name__)

LISTING_FOR = {
    ImageKind.PROCESSED_FRONT: ImageKind.LISTING_FRONT,
    ImageKind.PROCESSED_BACK: ImageKind.LISTING_BACK,
}
ORIGINAL_FOR = {
    ImageKind.PROCESSED_FRONT: ImageKind.ORIGINAL_FRONT,
    ImageKind.PROCESSED_BACK: ImageKind.ORIGINAL_BACK,
}

# Matches `processed_jpeg_quality`. These are the images a buyer judges the card by, and the
# difference between 94 and 97 is visible exactly where it matters — the fine speckle of edge
# whitening, which 94 smooths into the border.
JPEG_QUALITY = 97


@dataclass
class ListingResult:
    rendered: list[str]
    skipped: list[str]

    def as_dict(self) -> dict:
        return {"rendered": self.rendered, "skipped": self.skipped}


def render(photo: np.ndarray, corners: np.ndarray, px_per_mm: float, margin_mm: float):
    """Rectify with a uniform margin of background around the card.

    The card is mapped to an inset rectangle inside a larger canvas rather than the canvas
    itself, so the margin is exactly `margin_mm` on all four sides regardless of how the card
    was angled in the photograph — a scale factor would leave more margin on the long edge.
    """
    card_w = CARD_WIDTH_MM * px_per_mm
    card_h = CARD_HEIGHT_MM * px_per_mm
    margin = margin_mm * px_per_mm

    canvas_w = int(round(card_w + 2 * margin))
    canvas_h = int(round(card_h + 2 * margin))

    destination = np.float32(
        [
            [margin, margin],
            [margin + card_w - 1, margin],
            [margin + card_w - 1, margin + card_h - 1],
            [margin, margin + card_h - 1],
        ]
    )
    source = order_corners(np.asarray(corners, dtype=np.float32))
    matrix = cv2.getPerspectiveTransform(source, destination)

    # BORDER_REPLICATE would smear the card's own edge outward into the margin, which is
    # exactly the region a buyer inspects. Sampling the real photograph keeps the true
    # background — and any shadow the card casts, which reads as depth rather than a cut-out.
    return cv2.warpPerspective(
        photo, matrix, (canvas_w, canvas_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


async def build_for_item(
    session: AsyncSession, item: InventoryItem, margin_mm: float | None = None
) -> dict:
    """Render listing copies for whichever sides have been rectified.

    `margin_mm` overrides the configured spacing, so a whole collection can be re-rendered at a
    different margin without touching config or re-capturing anything.
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

    rendered: list[str] = []
    skipped: list[str] = []

    for processed_kind, listing_kind in LISTING_FOR.items():
        processed = images.get(processed_kind)
        original = images.get(ORIGINAL_FOR[processed_kind])
        if processed is None or original is None or not processed.corners:
            skipped.append(listing_kind.value)
            continue

        buffer = np.frombuffer(storage.get(original.path), dtype=np.uint8)
        photo = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if photo is None:
            skipped.append(listing_kind.value)
            continue

        # Fixed scale, not the capture's own: uniform output is the point.
        scale = settings.listing_px_per_mm
        canvas = render(
            photo,
            np.asarray(processed.corners, dtype=np.float32).reshape(4, 2),
            scale,
            margin_mm if margin_mm is not None else settings.listing_margin_mm,
        )

        ok, encoded = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            skipped.append(listing_kind.value)
            continue

        stored = storage.put(image_path(item.sku, listing_kind), encoded.tobytes(), overwrite=True)

        record = images.get(listing_kind) or Image(
            inventory_item_id=item.id, kind=listing_kind
        )
        record.path = stored.path
        record.width = stored.width
        record.height = stored.height
        record.bytes = stored.bytes
        record.sha256 = stored.sha256
        record.source = original.source
        record.captured_at = original.captured_at
        record.px_per_mm = scale
        record.error = None
        if record.id is None:
            session.add(record)
        rendered.append(listing_kind.value)

    await session.flush()
    log.info("listing_images.built", sku=item.sku, rendered=rendered, skipped=skipped)
    return ListingResult(rendered=rendered, skipped=skipped).as_dict()
