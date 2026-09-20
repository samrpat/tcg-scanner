"""Re-cut a card's crop from its recognised artwork.

Edge detection has to infer where a card ends from gradients in a photograph, and it can be
fooled by an inner frame, a play mat, or the card's own artwork panel. Once the card is
identified, none of that guesswork is necessary: the official art *is* the card, edge to edge,
so registering it against the photograph and projecting its corners gives the boundary directly
— from hundreds of matched features rather than four fitted lines.

This is what makes a listing photograph look deliberate rather than automated. It also fixes
orientation for free, because the homography knows which way up the art was.

The new crop replaces the old one only when it is measurably better. A recognition that
registers weakly should not be allowed to make a good crop worse.
"""

from dataclasses import dataclass

import cv2
import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ImageKind
from app.imaging.dewarp import dewarp, effective_px_per_mm
from app.imaging.geometry import order_corners
from app.imaging.quality import assess_quality, frame_margin
from app.imaging.register import register
from app.imaging.verify import border_evidence
from app.logging_setup import get_logger
from app.models import Card, Image, InventoryItem
from app.services.processing import PROCESSED_FOR, _reconcile_review, _store_processed
from app.storage import get_storage

log = get_logger(__name__)

# Registration against a card's own art is a much stronger signal than against an arbitrary
# candidate, so these can be strict. A weak match here means the identification is wrong.
# TCGdex art is the card edge to edge, measured against line-fitted card edges on ten real
# captures at 1.0044 wide by 1.0047 tall — accurate to under half a percent. The outset here is
# not correcting the art; it is insurance against the homography itself, which is less precise
# on holo and full-art cards where foil disrupts feature matching (76-158 inliers against
# 179-280 on ordinary cards). Erring outward costs a sliver of mat; erring inward clips the
# border where edge wear is measured.
ART_OUTSET = 1.012

# Registration precision is not uniform. Ordinary cards return 179-280 inliers; holo and
# full-art cards return 76-158, because foil breaks up the very texture ORB keys on. The
# projected quad on those is slightly small and slightly displaced, and a fixed outset cannot
# absorb it — the card overflowed the listing margin on both full-arts in the last batch while
# every ordinary card sat centred.
#
# So the outset scales with the evidence: plenty of inliers means the corners can be trusted
# tightly, few means leave room. Erring outward costs a sliver of mat, which the listing margin
# hides anyway; erring inward clips the card and is visible immediately.
OUTSET_AT_MIN_INLIERS = 1.035
CONFIDENT_INLIERS = 200


def _outset_for(inliers: int) -> float:
    """Interpolate the safety outset from how well the card registered."""
    if inliers >= CONFIDENT_INLIERS:
        return ART_OUTSET
    if inliers <= MIN_INLIERS:
        return OUTSET_AT_MIN_INLIERS
    span = (inliers - MIN_INLIERS) / (CONFIDENT_INLIERS - MIN_INLIERS)
    return OUTSET_AT_MIN_INLIERS + span * (ART_OUTSET - OUTSET_AT_MIN_INLIERS)

MIN_INLIERS = 25
MIN_INLIER_RATIO = 0.30

# Registration against the card's own art is decisive evidence — 179 to 280 inliers across the
# first ten real captures, where a wrong candidate yields none. Above this the art crop is taken
# as authoritative rather than merely a contender.
DECISIVE_INLIERS = 60

# Border evidence does NOT get a veto over a decisive registration.
#
# It measures how sharply the card differs from the background at the crop boundary, which
# assumes the card's edge is high-contrast against the mat. That assumption fails on exactly the
# cards it was consulted about: a dark blue back on a black mat, and a full-art card whose
# border *is* the artwork. In both cases it scored a correct crop lower than a wrong one — it
# rejected the art crop on two captures of the same Gourgeist ex whose edge-detected crops were
# visibly cut off at the top, bottom and left.
#
# Registration has no such blind spot. It either matches the card's own artwork across hundreds
# of features or it does not. So evidence only arbitrates when registration is too weak to
# decide by itself.
WEAK_REGISTRATION_TOLERANCE = 0.06


@dataclass
class RecropResult:
    replaced: bool
    reason: str
    before: float = 0.0
    after: float = 0.0
    inliers: int = 0
    rotation_deg: float = 0.0

    def as_dict(self) -> dict:
        return {
            "replaced": self.replaced,
            "reason": self.reason,
            "border_before": round(self.before, 3),
            "border_after": round(self.after, 3),
            "inliers": self.inliers,
            "rotation_deg": round(self.rotation_deg, 1),
        }


async def _art(client: httpx.AsyncClient, url: str) -> np.ndarray | None:
    try:
        response = await client.get(f"{url}/high.jpg")
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("recrop.art_failed", url=url, error=str(exc))
        return None
    decoded = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    return decoded if decoded is not None and decoded.size else None


def _decode(storage, image: Image | None) -> np.ndarray | None:
    """Decode a stored original at full resolution.

    Full resolution deliberately, and it is not the inconsistency it looks like. This path
    produces the front a buyer actually sees, and registration against the reference art costs
    0.02-0.05 s whatever the input size, because ORB downscales internally. There is nothing to
    save here and a great deal to lose: every pixel dropped before the warp is detail the
    listing image cannot get back.

    It also keeps the corners this writes in the original file's coordinate space, which is the
    space they are stored, redisplayed and edited in.
    """
    if image is None:
        return None
    buffer = np.frombuffer(storage.get(image.path), dtype=np.uint8)
    decoded = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    return decoded if decoded is not None and decoded.size else None


async def recrop_front(session: AsyncSession, sku: str, user_id) -> dict:
    """Re-cut the front crop using the identified card's artwork."""
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.sku == sku, InventoryItem.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if item is None:
        return {"ok": False, "error": f"no such card: {sku}"}
    if item.card_id is None:
        return {"ok": False, "sku": sku, "error": "not identified yet"}

    card = (await session.execute(select(Card).where(Card.id == item.card_id))).scalar_one()
    if not card.image_url:
        return {"ok": False, "sku": sku, "error": "identified card has no art"}

    images = {
        image.kind: image
        for image in (
            await session.execute(select(Image).where(Image.inventory_item_id == item.id))
        )
        .scalars()
        .all()
    }
    original = images.get(ImageKind.ORIGINAL_FRONT)
    if original is None:
        return {"ok": False, "sku": sku, "error": "no original front"}

    storage = get_storage()
    photo = _decode(storage, original)
    if photo is None:
        return {"ok": False, "sku": sku, "error": "original could not be decoded"}

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        art = await _art(client, card.image_url)
    if art is None:
        return {"ok": False, "sku": sku, "error": "art could not be fetched"}

    match = register(photo, art, min_inliers=MIN_INLIERS, min_inlier_ratio=MIN_INLIER_RATIO)
    if match is None:
        return RecropResult(False, "art did not register against the original").as_dict() | {
            "ok": True,
            "sku": sku,
        }

    # Deliberately NOT re-ordered by image position: these corners come from the reference art
    # through the homography, so the first is the card's true top-left however the card was
    # lying. Keeping that order is what corrects a card photographed upside-down or sideways.
    projected = np.asarray(match.corners, dtype=np.float32).reshape(4, 2)
    centre = projected.mean(axis=0)
    corners = (centre + (projected - centre) * _outset_for(match.inliers)).astype(np.float32)
    before = border_evidence(photo, order_corners(_existing_corners(images, photo)))
    after = border_evidence(photo, corners)

    decisive = match.inliers >= DECISIVE_INLIERS
    if not decisive and after.score < before.score - WEAK_REGISTRATION_TOLERANCE:
        return RecropResult(
            False,
            "registration weak and the art crop measured worse",
            before.score,
            after.score,
            match.inliers,
            match.rotation_deg,
        ).as_dict() | {"ok": True, "sku": sku}

    source_scale = effective_px_per_mm(corners)
    result = dewarp(photo, corners, _choose_scale(source_scale), oriented=True)
    height, width = photo.shape[:2]
    quality = assess_quality(
        result.image,
        source_scale,
        margin_px=frame_margin(corners, width, height),
        area_fraction=None,
    )

    from app.imaging.detect import Detection

    detection = Detection(
        corners=corners,
        confidence=round(min(1.0, match.inlier_ratio), 4),
        method="artref",
        area_fraction=0.0,
        aspect=0.0,
        edge_fit=match.as_dict(),
    )
    await _store_processed(session, item, original, result, detection, quality)
    await session.flush()
    await _reconcile_review(session, item)
    await session.flush()

    log.info(
        "recrop.replaced",
        sku=sku,
        inliers=match.inliers,
        before=round(before.score, 3),
        after=round(after.score, 3),
    )
    return RecropResult(
        True, "re-cut from artwork", before.score, after.score, match.inliers, match.rotation_deg
    ).as_dict() | {"ok": True, "sku": sku}


def _existing_corners(images: dict, photo: np.ndarray) -> np.ndarray:
    """Corners the current processed image was cut from, or the whole frame if unknown."""
    processed = images.get(PROCESSED_FOR[ImageKind.ORIGINAL_FRONT])
    if processed is not None and processed.corners:
        return np.asarray(processed.corners, dtype=np.float32).reshape(4, 2)
    height, width = photo.shape[:2]
    return np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])


def _choose_scale(source_px_per_mm: float | None) -> float:
    from app.imaging.pipeline import choose_scale

    return choose_scale(source_px_per_mm)
