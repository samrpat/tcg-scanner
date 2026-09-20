"""Run the imaging pipeline against a stored original and record the outcome.

Never destroys an original. A failure here leaves the photograph intact and opens a review,
because a re-run after a fix is cheap and a re-shoot is not.
"""

import uuid
from datetime import datetime

import cv2
import numpy as np
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.enums import ImageKind, QualityVerdict, ReviewCategory, ReviewStatus
from app.imaging.detect import Detection
from app.imaging.dewarp import dewarp, effective_px_per_mm
from app.imaging.edges import refine_quad
from app.imaging.pipeline import (
    choose_scale,
    decode_full,
    encode_jpeg,
    process_capture,
)
from app.imaging.quality import assess_quality
from app.logging_setup import get_logger
from app.models import Image, InventoryItem, Review
from app.storage import get_storage, image_path

log = get_logger(__name__)

# How far the edge fitter may move a hand-placed corner before its answer is ignored.
MANUAL_SNAP_LIMIT_PX = 14.0

PROCESSED_FOR = {
    ImageKind.ORIGINAL_FRONT: ImageKind.PROCESSED_FRONT,
    ImageKind.ORIGINAL_BACK: ImageKind.PROCESSED_BACK,
}


async def _problems(session: AsyncSession, item: InventoryItem) -> list[dict]:
    """Every current complaint about this item's images.

    Recomputed from stored state rather than accumulated, so a successful reprocess of one
    side clears that side's complaint without needing to know what the old one said.
    """
    images = (
        (
            await session.execute(select(Image).where(Image.inventory_item_id == item.id))
        )
        .scalars()
        .all()
    )
    by_kind = {image.kind: image for image in images}
    found: list[dict] = []

    for original_kind, processed_kind in PROCESSED_FOR.items():
        original = by_kind.get(original_kind)
        if original is None:
            continue

        if original.error:
            found.append({"kind": original_kind.value, "reason": original.error})
            continue

        processed = by_kind.get(processed_kind)
        if processed is None:
            # Still queued. Not a problem — the other side of the card is very likely being
            # processed concurrently, and complaining about it would open a review that
            # resolves itself a second later.
            continue

        reasons: list[str] = []
        confidence = (
            float(processed.detection_confidence)
            if processed.detection_confidence is not None
            else 0.0
        )
        if confidence < settings.detection_min_confidence:
            reasons.append(
                f"low detection confidence {confidence:.2f} "
                f"(threshold {settings.detection_min_confidence:.2f})"
            )
        verdict = processed.quality_verdict
        if verdict is not None and verdict is not QualityVerdict.OK:
            detail = (processed.quality or {}).get("reasons") or [verdict.value]
            reasons.extend(detail)
        if reasons:
            found.append({"kind": processed_kind.value, "reason": "; ".join(reasons)})

    return found


async def _lock_item(session: AsyncSession, item: InventoryItem) -> None:
    """Take the item's write lock before touching anything that belongs to it.

    The two sides of a card are processed by two workers at the same time, and both write an
    `images` row for the same inventory item. In Postgres that insert takes a `FOR KEY SHARE`
    lock on the parent row, so by the time either transaction asked for `FOR UPDATE` — which
    `_reconcile_review` did — both already held a weaker lock on it and each was waiting for
    the other to release. Postgres broke the tie by killing one, and the side it killed lost
    its processed image silently: the capture looked fine, the job was simply gone.

    Taking the exclusive lock *first*, before any child write, removes the upgrade entirely.
    The second worker blocks until the first commits, which costs a moment and is the whole
    point.
    """
    await session.execute(
        select(InventoryItem.id).where(InventoryItem.id == item.id).with_for_update()
    )


async def _reconcile_review(session: AsyncSession, item: InventoryItem) -> bool:
    """Open, update or resolve this item's image review to match reality.

    Returns True if a review is open afterwards. Resolving on success matters: without it a
    reprocessed card stays in the review queue forever showing a reason that is no longer true.
    """
    # Lock the item so the two sides of a card cannot reconcile concurrently. Without this
    # both workers read "no open review" and both insert one. Re-requesting a lock this
    # transaction already holds is free, so callers that locked earlier pay nothing.
    await _lock_item(session, item)

    problems = await _problems(session, item)
    existing = (
        await session.execute(
            select(Review).where(
                Review.inventory_item_id == item.id,
                Review.category == ReviewCategory.IMAGE,
                Review.status == ReviewStatus.OPEN,
            )
        )
    ).scalar_one_or_none()

    if not problems:
        if existing:
            existing.status = ReviewStatus.RESOLVED
            existing.resolved_at = datetime.now().astimezone()
            existing.resolution = {"resolved_by": "reprocess", "note": "all images now clean"}
        return False

    reason = "; ".join(f"{p['kind']}: {p['reason']}" for p in problems)
    if existing:
        existing.reason = reason
        existing.candidates = problems
    else:
        session.add(
            Review(
                user_id=item.user_id,
                inventory_item_id=item.id,
                category=ReviewCategory.IMAGE,
                reason=reason,
                candidates=problems,
            )
        )
    return True


async def _store_processed(
    session: AsyncSession,
    item: InventoryItem,
    original: Image,
    dewarped,
    detection,
    quality,
) -> Image:
    """Write the rectified image and its metadata.

    Shared by automatic and manual rectification so the two paths cannot drift — a manually
    corrected card must carry exactly the same fields as an automatically detected one, or
    Phase 3 has to special-case it.

    Overwrites the processed image only. The original is never touched (REQ-STO-002).
    """
    processed_kind = PROCESSED_FOR[original.kind]
    path = image_path(item.sku, processed_kind)
    stored = get_storage().put(path, encode_jpeg(dewarped.image), overwrite=True)

    processed = (
        await session.execute(
            select(Image).where(
                Image.inventory_item_id == item.id, Image.kind == processed_kind
            )
        )
    ).scalar_one_or_none() or Image(inventory_item_id=item.id, kind=processed_kind)

    processed.path = stored.path
    processed.width = stored.width
    processed.height = stored.height
    processed.bytes = stored.bytes
    processed.sha256 = stored.sha256
    processed.source = original.source
    processed.captured_at = original.captured_at
    processed.px_per_mm = dewarped.px_per_mm
    processed.rotation_applied = dewarped.rotation_applied
    processed.detection_confidence = detection.confidence if detection else None
    processed.detection_method = detection.method if detection else None
    processed.corners = detection.as_list() if detection else None
    processed.quality_verdict = quality.verdict if quality else None
    processed.quality = quality.as_dict() if quality else None
    processed.error = None
    if processed.id is None:
        session.add(processed)

    original.error = None
    original.quality = quality.as_dict() if quality else None
    return processed


async def process_image(session: AsyncSession, image_id: uuid.UUID) -> dict:
    """Detect, rectify and assess one stored original."""
    original = await session.get(Image, image_id)
    if original is None:
        return {"ok": False, "error": "image not found"}
    if original.kind not in PROCESSED_FOR:
        return {"ok": False, "error": f"{original.kind.value} is not an original"}

    item = await session.get(InventoryItem, original.inventory_item_id)
    if item is None:
        return {"ok": False, "error": "inventory item not found"}

    # Before any write that touches this item — see `_lock_item`.
    await _lock_item(session, item)

    storage = get_storage()
    payload = storage.get(original.path)

    # No explicit scale: let the pipeline match the capture (docs/IMAGING.md).
    result = process_capture(payload)
    processed_kind = PROCESSED_FOR[original.kind]

    if not result.ok or result.dewarped is None:
        original.error = result.error
        if result.quality:
            original.quality = result.quality.as_dict()
        await session.flush()
        await _reconcile_review(session, item)
        await session.flush()
        log.warning("processing.failed", sku=item.sku, kind=original.kind.value,
                    error=result.error)
        return {"ok": False, "sku": item.sku, "error": result.error}

    await _store_processed(
        session, item, original, result.dewarped, result.detection, result.quality
    )
    await session.flush()
    # A new capture is the answer to a rescan request, so the flag clears itself. Leaving it to
    # the operator would mean the Rescan list slowly filled with cards that had already been
    # re-shot.
    item.rescan_requested_at = None
    needs_review = await _reconcile_review(session, item)
    await session.flush()

    log.info(
        "processing.done",
        sku=item.sku,
        kind=processed_kind.value,
        confidence=float(result.detection.confidence) if result.detection else None,
        verdict=result.quality.verdict.value if result.quality else None,
    )
    return {
        "ok": True,
        "sku": item.sku,
        "kind": processed_kind.value,
        "confidence": float(result.detection.confidence) if result.detection else None,
        "verdict": result.quality.verdict.value if result.quality else None,
        "needs_review": needs_review,
    }


async def process_with_corners(
    session: AsyncSession, image_id: uuid.UUID, corners: list[list[float]]
) -> dict:
    """Rectify using corners the operator placed by hand.

    Detection will never be perfect on a hand-held photograph of a card against a cluttered
    background, and a card that cannot be rectified is a card that cannot be sold. This turns
    every detection failure from a dead end into a few seconds of dragging.
    """
    if len(corners) != 4:
        raise ValueError(f"expected 4 corners, got {len(corners)}")

    original = await session.get(Image, image_id)
    if original is None:
        return {"ok": False, "error": "image not found"}
    if original.kind not in PROCESSED_FOR:
        return {"ok": False, "error": f"{original.kind.value} is not an original"}

    item = await session.get(InventoryItem, original.inventory_item_id)
    if item is None:
        return {"ok": False, "error": "inventory item not found"}

    # Full resolution, always: the operator placed these corners on the full-size original in
    # the editor, so they are in that image's coordinates. Decoding smaller here would land the
    # quad in the top-left quadrant of the picture it was drawn on.
    payload = decode_full(get_storage().get(original.path))
    if payload is None:
        return {"ok": False, "error": "could not decode the stored original"}

    points = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    height, width = payload.shape[:2]
    if (points < 0).any() or (points[:, 0] > width).any() or (points[:, 1] > height).any():
        raise ValueError("corners fall outside the image")

    # Snap the operator's rough placement onto the real intensity edge. A hand placement is
    # good to maybe ten pixels; line fitting from there is good to a fraction of one. The
    # snap is only accepted if it stays close to where the corners were put, so it sharpens
    # the intent rather than overriding it.
    snapped = "manual"
    grey = cv2.cvtColor(payload, cv2.COLOR_BGR2GRAY)
    fitted = refine_quad(grey, points)
    if fitted is not None:
        candidate, diagnostics = fitted
        if diagnostics["max_shift_px"] <= MANUAL_SNAP_LIMIT_PX:
            points = candidate.astype(np.float32)
            snapped = "manual+lines"
            log.info("processing.manual_snapped", **diagnostics)

    await _lock_item(session, item)

    source_scale = effective_px_per_mm(points)
    result = dewarp(payload, points, choose_scale(source_scale))
    quality = assess_quality(result.image, source_scale)

    # Recorded as a full-confidence manual detection: a human placed these corners, so there
    # is nothing uncertain about them, and Phase 3 should not treat them as a weak signal.
    detection = Detection(
        corners=points,
        confidence=1.0,
        method=snapped,
        area_fraction=0.0,
        aspect=0.0,
    )
    await _store_processed(session, item, original, result, detection, quality)
    await session.flush()
    # A new capture is the answer to a rescan request, so the flag clears itself. Leaving it to
    # the operator would mean the Rescan list slowly filled with cards that had already been
    # re-shot.
    item.rescan_requested_at = None
    needs_review = await _reconcile_review(session, item)
    await session.flush()

    log.info("processing.manual", sku=item.sku, kind=original.kind.value)
    return {
        "ok": True,
        "sku": item.sku,
        "kind": PROCESSED_FOR[original.kind].value,
        "verdict": quality.verdict.value,
        "needs_review": needs_review,
    }


async def find_incomplete_renders(session: AsyncSession, user_id=None) -> list:
    """Cards whose sides are processed but whose listing renders are missing.

    A separate failure from a lost job: the original processed fine, so `find_unprocessed` sees
    nothing wrong, yet the card has no listing image and never will unless something notices.
    Four of thirty cards were in exactly this state, invisible, because the render was only ever
    triggered from the front's recognition.

    Anything that can silently leave a card half-finished has to be checkable, or the operator
    ends up not trusting any of the progress reporting — which is the real cost.
    """
    from app.models import Image as ImageModel

    def has(kind: ImageKind):
        return (
            select(ImageModel.id)
            .where(
                ImageModel.inventory_item_id == InventoryItem.id,
                ImageModel.kind == kind,
            )
            .exists()
        )

    query = select(InventoryItem).where(
        or_(
            and_(has(ImageKind.PROCESSED_FRONT), ~has(ImageKind.LISTING_FRONT)),
            and_(has(ImageKind.PROCESSED_BACK), ~has(ImageKind.LISTING_BACK)),
        )
    )
    if user_id is not None:
        query = query.where(InventoryItem.user_id == user_id)
    return list((await session.execute(query)).scalars().all())


async def find_unprocessed(session: AsyncSession, user_id: uuid.UUID | None = None) -> list[Image]:
    """Originals that have no processed counterpart.

    A queued job can be lost — a worker restart mid-flight, a Redis eviction, an enqueue that
    raced a deploy. When that happens the capture is stored, no error is recorded and no review
    is opened, so the card simply sits there looking captured and never becomes gradeable. One
    lost job in twenty is invisible; at two thousand cards it is a silent hole in the inventory.

    Cheap to check, so it is checked rather than assumed.
    """
    counterpart = aliased(Image)
    stale: list[Image] = []

    for original_kind, processed_kind in PROCESSED_FOR.items():
        exists_processed = (
            select(counterpart.id)
            .where(
                counterpart.inventory_item_id == Image.inventory_item_id,
                counterpart.kind == processed_kind,
            )
            .exists()
        )
        query = (
            select(Image)
            .join(InventoryItem, InventoryItem.id == Image.inventory_item_id)
            .where(Image.kind == original_kind, Image.error.is_(None), ~exists_processed)
        )
        if user_id is not None:
            query = query.where(InventoryItem.user_id == user_id)
        stale.extend((await session.execute(query)).scalars().all())

    return stale
