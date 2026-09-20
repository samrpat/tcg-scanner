"""Capture endpoints.

Every one of these returns as soon as the original is on disk. Detection and dewarp go to the
queue, so the shutter is never blocked by the pipeline (D-005).
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_session
from app.enums import CaptureSource, ImageKind, ReviewCategory, ReviewStatus
from app.models import Card, CardVariant, Image, InventoryItem, Review, ScanSession, User
from app.services.capture import (
    CaptureError,
    attach_image,
    capture,
    find_item_awaiting_back,
    pair_batch,
)
from app.services.listing_images import build_for_item
from app.services.processing import find_unprocessed, process_with_corners
from app.services.recognition import recognise_and_finish, resolve_variant
from app.services.recrop import recrop_front
from app.services.seed import DEFAULT_USER_EMAIL
from app.storage.paths import EXTRA_KINDS

router = APIRouter(prefix="/capture", tags=["capture"])


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Whoever the session cookie belongs to.

    The middleware in `app.authz` has already resolved and refused; by the time a route body
    runs, `request.state.user_id` is set or the request never got here. This only loads the
    row — and falls back to the seeded user when authentication is switched off, which is the
    single-user behaviour this had before there was a front door.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        user = await session.get(User, uuid.UUID(user_id))
        if user is not None:
            return user

    user = (
        await session.execute(select(User).where(User.email == DEFAULT_USER_EMAIL))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=503, detail="no user seeded; run `make seed`")
    return user


async def _enqueue_processing(image_id: uuid.UUID) -> str | None:
    pool = await create_pool(RedisSettings(host=settings.redis_host, port=settings.redis_port))
    try:
        job = await pool.enqueue_job("process_image_task", str(image_id))
        return job.job_id if job else None
    finally:
        await pool.aclose()


async def _enqueue_recognition(sku: str, user_id: uuid.UUID) -> str | None:
    pool = await create_pool(RedisSettings(host=settings.redis_host, port=settings.redis_port))
    try:
        job = await pool.enqueue_job("recognise_task", sku, str(user_id))
        return job.job_id if job else None
    finally:
        await pool.aclose()


# The four renders every finished card has. Always reported, present or not: a missing image and
# an image nobody asked about look identical when the slot is simply omitted, and the operator
# cannot tell "still processing" from "this failed" from "I never shot the back".
IMAGE_SLOTS = (
    ("Listing front", ImageKind.LISTING_FRONT, ImageKind.ORIGINAL_FRONT),
    ("Listing back", ImageKind.LISTING_BACK, ImageKind.ORIGINAL_BACK),
    ("Processed front", ImageKind.PROCESSED_FRONT, ImageKind.ORIGINAL_FRONT),
    ("Processed back", ImageKind.PROCESSED_BACK, ImageKind.ORIGINAL_BACK),
)


def _image_slots(by_kind: dict) -> list[dict]:
    """Every render a card should have, with why it is missing when it is.

    `state` is one of ready / pending / failed / uncaptured, so the caller can say something
    useful in the gap instead of quietly leaving one out.
    """
    slots = []
    for label, kind, source_kind in IMAGE_SLOTS:
        image = by_kind.get(kind)
        source = by_kind.get(source_kind)
        if image is not None:
            state, note = "ready", None
        elif source is None:
            state, note = "uncaptured", f"no {source_kind.value.split('_')[-1]} captured yet"
        elif source.error:
            state, note = "failed", source.error
        else:
            # The original is there and undamaged, so the render is queued or in flight.
            state, note = "pending", "processing…"
        slots.append(
            {"label": label, "url": image_url(image), "state": state, "note": note}
        )
    return slots


def _variant_label(variant) -> str | None:
    """Human-readable variant name, e.g. "Reverse Holo" or "Normal · set logo".

    TCGdex models the finish as a set of booleans rather than one field, so the label is derived.

    Stamp and subtype are part of the label, not decoration. A card can be printed in the same
    finish twice and differ only by a stamp — ex14-49 has two "normal" variants whose sole
    difference is a set-logo stamp — and those sell for different money. Labelling both "Normal"
    put two identical-looking buttons in front of the operator with no way to choose between
    them, which is worse than offering no choice at all.
    """
    if variant is None:
        return None

    if variant.is_reverse:
        finish = "Reverse Holo"
    elif variant.is_holo:
        finish = "Holo"
    elif variant.is_normal:
        finish = "Normal"
    else:
        finish = (variant.type or "").replace("_", " ").title() or "Variant"

    qualifiers = [
        q.replace("_", " ").replace("-", " ")
        for q in (variant.subtype, variant.stamp_key)
        if q
    ]
    return f"{finish} · {' · '.join(qualifiers)}" if qualifiers else finish


def image_url(image: Image | None) -> str | None:
    """URL for a stored image, versioned by its content hash.

    Processed images are rewritten in place — reprocessing and manual corner correction both
    overwrite the same path — while the images route serves them `immutable` with a one-year
    max-age. Without a version token the browser is explicitly told never to revalidate, so a
    corrected card keeps showing its old crop forever.

    Hashing the content rather than stamping a timestamp means the URL only changes when the
    pixels do, so the long cache lifetime still does its job.
    """
    if image is None:
        return None
    base = f"/api/images/{image.path}"
    return f"{base}?v={image.sha256[:12]}" if image.sha256 else base


async def _read(upload: UploadFile) -> bytes:
    payload = await upload.read()
    if len(payload) > settings.capture_max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"image is {len(payload)} bytes, over the "
            f"{settings.capture_max_bytes} byte limit",
        )
    if not payload:
        raise HTTPException(status_code=400, detail="empty upload")
    return payload


@router.post("")
async def capture_side(
    file: UploadFile = File(...),
    side: str = Form(...),
    source: CaptureSource = Form(CaptureSource.UPLOAD),
    replace: bool = Form(False),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Capture one side. A front starts a new card; a back finishes the waiting one."""
    payload = await _read(file)
    try:
        result = await capture(
            session,
            user_id=user.id,
            side=side,
            payload=payload,
            source=source,
            replace=replace,
        )
    except CaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    job_id = await _enqueue_processing(result.image.id)

    return {
        "sku": result.item.sku,
        "item_id": str(result.item.id),
        "image_id": str(result.image.id),
        "side": side,
        "created_item": result.created_item,
        "next_side": result.next_side,
        "processing_job": job_id,
    }


@router.post("/batch")
async def capture_batch(
    files: list[UploadFile] = File(...),
    source: CaptureSource = Form(CaptureSource.UPLOAD),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Upload several images at once, paired by filename hint or arrival order."""
    if not files:
        raise HTTPException(status_code=400, detail="no files")

    sides = pair_batch([f.filename or "" for f in files])
    results = []
    for upload, side in zip(files, sides, strict=True):
        payload = await _read(upload)
        try:
            outcome = await capture(
                session, user_id=user.id, side=side, payload=payload, source=source
            )
        except CaptureError as exc:
            results.append({"filename": upload.filename, "side": side, "error": str(exc)})
            continue
        await session.commit()
        await _enqueue_processing(outcome.image.id)
        results.append(
            {"filename": upload.filename, "side": side, "sku": outcome.item.sku,
             "image_id": str(outcome.image.id)}
        )

    return {"accepted": sum(1 for r in results if "error" not in r), "results": results}


class CornersIn(BaseModel):
    """Four corners in the ORIGINAL image's pixel coordinates, any order."""

    corners: list[tuple[float, float]] = Field(min_length=4, max_length=4)


# --- Static paths first -------------------------------------------------------------
# FastAPI matches routes in declaration order, so every literal path has to be declared
# before the /{sku} patterns. Declared after, GET /capture/pending resolves as a card
# named "pending" and 404s.

@router.post("/extra")
async def capture_extra_for_current(
    file: UploadFile = File(...),
    source: CaptureSource = Form(CaptureSource.UPLOAD),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Attach an extra shot to the card being worked on.

    The quick path, for the phone screen: the operator has just shot a holo's front and back,
    can see the foil catching the light, and wants that in the listing. Asking them to find the
    card in a list first would make it a thing they stop doing.

    "The card being worked on" is the newest one, which is what it means while a pile is being
    shot. The SKU comes back in the response and the screen shows it, so a shot that landed on
    the wrong card is visible immediately rather than discovered at upload.
    """
    item = (
        await session.execute(
            select(InventoryItem)
            .options(selectinload(InventoryItem.images))
            .where(InventoryItem.user_id == user.id)
            .order_by(desc(InventoryItem.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=409, detail="no cards yet — shoot a front before an extra shot"
        )
    return await _attach_extra(session, item, file, source)


@router.get("/pending")
async def pending(
    session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> dict:
    """What the operator should shoot next, and how the queue is doing."""
    waiting = await find_item_awaiting_back(session, user.id)
    total = (
        await session.execute(
            select(func.count()).select_from(InventoryItem).where(InventoryItem.user_id == user.id)
        )
    ).scalar_one()
    open_reviews = (
        await session.execute(
            select(func.count())
            .select_from(Review)
            .where(
                Review.user_id == user.id,
                Review.status == ReviewStatus.OPEN,
                Review.category == ReviewCategory.IMAGE,
            )
        )
    ).scalar_one()
    # Captures whose processing job never ran. Surfaced here because the failure is otherwise
    # invisible: no error, no review, just a card that quietly never becomes gradeable.
    unprocessed = await find_unprocessed(session, user.id)

    return {
        "next_side": "back" if waiting else "front",
        "awaiting_back": waiting.sku if waiting else None,
        "items": total,
        "open_image_reviews": open_reviews,
        "unprocessed": len(unprocessed),
    }


@router.get("/recent")
async def recent(
    limit: int = Query(12, le=50),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> list[dict]:
    """Recent captures with their processing state, for the capture strip."""
    items = (
        (
            await session.execute(
                select(InventoryItem)
                .options(
                    selectinload(InventoryItem.images),
                    selectinload(InventoryItem.card),
                    selectinload(InventoryItem.card_variant),
                )
                .where(InventoryItem.user_id == user.id)
                .order_by(desc(InventoryItem.created_at))
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    # Which of these batches identify their cards. The capture strip says "identifying…"
    # while a card has no name yet, and in a photos-only batch nothing ever will — so without
    # this the newest card sits there claiming to be mid-identification for ever.
    batch_ids = {item.session_id for item in items if item.session_id is not None}
    photos_only: dict = {}
    if batch_ids:
        photos_only = {
            row.id: row.photos_only
            for row in (
                await session.execute(select(ScanSession).where(ScanSession.id.in_(batch_ids)))
            )
            .scalars()
            .all()
        }

    def side_state(by_kind: dict, original_kind: ImageKind, processed_kind: ImageKind) -> dict:
        original = by_kind.get(original_kind)
        processed = by_kind.get(processed_kind)
        return {
            "captured": original is not None,
            "processed": processed is not None,
            "manual": bool(processed and processed.detection_method == "manual"),
            "confidence": float(processed.detection_confidence)
            if processed and processed.detection_confidence is not None
            else None,
            "verdict": processed.quality_verdict.value
            if processed and processed.quality_verdict
            else None,
            "error": original.error if original else None,
        }

    payload = []
    for item in items:
        by_kind = {image.kind: image for image in item.images}
        processed_front = by_kind.get(ImageKind.PROCESSED_FRONT)
        front = side_state(by_kind, ImageKind.ORIGINAL_FRONT, ImageKind.PROCESSED_FRONT)
        back = side_state(by_kind, ImageKind.ORIGINAL_BACK, ImageKind.PROCESSED_BACK)
        payload.append(
            {
                "sku": item.sku,
                "created_at": item.created_at.isoformat(),
                "photos_only": bool(photos_only.get(item.session_id)),
                # Extra shots this card already carries. The scan screen shows the count on
                # its button, so a second tap on the same card is a deliberate second shot
                # rather than a wondered-whether-that-worked duplicate.
                "extras": [
                    {"slot": i + 1, "url": image_url(by_kind[kind])}
                    for i, kind in enumerate(EXTRA_KINDS)
                    if kind in by_kind
                ],
                "has_front": front["captured"],
                "has_back": back["captured"],
                "processed_front": front["processed"],
                "processed_back": back["processed"],
                "front": front,
                "back": back,
                # The listing renders are what a buyer actually sees, so they are what the
                # capture strip shows. The processed images stay available for inspection —
                # they are the grading artefacts, not the presentation ones.
                "thumbnail": (
                    image_url(by_kind.get(ImageKind.LISTING_FRONT))
                    or image_url(processed_front)
                ),
                "listing_front": image_url(by_kind.get(ImageKind.LISTING_FRONT)),
                "listing_back": image_url(by_kind.get(ImageKind.LISTING_BACK)),
                "processed_front_url": image_url(processed_front),
                "processed_back_url": image_url(by_kind.get(ImageKind.PROCESSED_BACK)),
                "confidence": front["confidence"],
                "verdict": front["verdict"],
                "errors": [i.error for i in item.images if i.error],
                "status": item.status.value,
                "card": item.card.name if item.card else None,
                "card_number": item.card.local_id if item.card else None,
                # The variant is a material part of the card's identity — a reverse holo and a
                # normal print of the same card are different things at different prices — so it
                # belongs in the title, not buried in a detail view.
                "variant": _variant_label(item.card_variant),
                "identified_confidence": float(item.identification_confidence)
                if item.identification_confidence is not None
                else None,
            }
        )
    return payload


# --- Parameterised paths ------------------------------------------------------------

@router.post("/{sku}/{side}/corners")
async def rectify_from_corners(
    sku: str,
    side: Literal["front", "back"],
    body: CornersIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Rectify a card from corners placed by hand.

    Runs inline rather than on the queue: the operator is looking at the result, and a manual
    correction that takes a round trip through Redis to show up feels broken.
    """
    item = (
        await session.execute(
            select(InventoryItem)
            .options(selectinload(InventoryItem.images))
            .where(InventoryItem.user_id == user.id, InventoryItem.sku == sku)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    kind = ImageKind.ORIGINAL_FRONT if side == "front" else ImageKind.ORIGINAL_BACK
    original = next((i for i in item.images if i.kind == kind), None)
    if original is None:
        raise HTTPException(status_code=404, detail=f"{sku} has no {side} image")

    try:
        result = await process_with_corners(
            session, original.id, [list(c) for c in body.corners]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()

    # Re-render the listing image from the corrected crop.
    #
    # Rectifying alone only rewrites the *processed* image. The listing render is derived from
    # it and was left untouched, so an operator who fixed a bad crop watched the big image on
    # the approve screen — the one a buyer sees, and the one they were correcting — carry on
    # showing the old mistake. The correction appeared to do nothing.
    #
    # Deliberately NOT re-running `recrop_front`: that re-cuts the boundary from the card's
    # reference art, which is exactly what the operator has just overruled by hand. Hand-placed
    # corners are the most authoritative source there is and nothing may quietly replace them.
    refreshed = (
        await session.execute(
            select(InventoryItem)
            .options(selectinload(InventoryItem.images))
            .where(InventoryItem.user_id == user.id, InventoryItem.sku == sku)
        )
    ).scalar_one_or_none()
    if refreshed is not None:
        try:
            result["listing_images"] = await build_for_item(session, refreshed)
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - the crop is saved either way
            result["listing_images"] = {"error": str(exc)}
    return result


# Registered before /{sku}/{side}: FastAPI matches in declaration order, and the
# two-segment pattern would otherwise swallow /{sku}/reprocess and demand a file upload.
@router.get("/{sku}")
async def get_item(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Everything the corner adjuster needs: the originals, their size, and any corners
    detection already proposed as a starting point."""
    item = (
        await session.execute(
            select(InventoryItem)
            .options(
                selectinload(InventoryItem.images),
                selectinload(InventoryItem.card),
                selectinload(InventoryItem.card_variant),
            )
            .where(InventoryItem.user_id == user.id, InventoryItem.sku == sku)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    by_kind = {i.kind: i for i in item.images}
    sides = {}
    for side, original_kind, processed_kind in (
        ("front", ImageKind.ORIGINAL_FRONT, ImageKind.PROCESSED_FRONT),
        ("back", ImageKind.ORIGINAL_BACK, ImageKind.PROCESSED_BACK),
    ):
        original = by_kind.get(original_kind)
        processed = by_kind.get(processed_kind)
        if original is None:
            continue
        sides[side] = {
            "original": image_url(original),
            "width": original.width,
            "height": original.height,
            "error": original.error,
            "processed": image_url(processed),
            "corners": processed.corners if processed else None,
            "confidence": float(processed.detection_confidence)
            if processed and processed.detection_confidence is not None
            else None,
            "method": processed.detection_method if processed else None,
            "manual": bool(processed and processed.detection_method == "manual"),
            "verdict": processed.quality_verdict.value
            if processed and processed.quality_verdict
            else None,
            "quality": processed.quality if processed else original.quality,
        }
    # The detail view needs identity and renders too, not just the originals it edits corners
    # on. Returning them here rather than having the client hunt for the card in /recent, which
    # is paginated and will not contain an older card at all.
    return {
        "sku": item.sku,
        "sides": sides,
        "card": (
            {
                "tcgdex_id": item.card.tcgdex_id,
                "name": item.card.name,
                "local_id": item.card.local_id,
            }
            if item.card
            else None
        ),
        "variant": _variant_label(item.card_variant),
        "confidence": float(item.identification_confidence)
        if item.identification_confidence is not None
        else None,
        "images": _image_slots(by_kind),
    }


@router.post("/{sku}/recognise")
@router.post("/{sku}/recognize")
async def recognise(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Identify the card this item holds, and record it.

    Runs inline rather than on the queue: the operator is looking at the result, and it takes
    about two seconds. Batch recognition belongs on the queue; a single card does not.
    """
    result = await recognise_and_finish(session, sku, user.id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "recognition failed"))
    return result


class CardIn(BaseModel):
    tcgdex_id: str


@router.post("/{sku}/card")
async def set_card(
    sku: str,
    body: CardIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Correct which card this is, by hand.

    Changing the card invalidates the variant: variants belong to a card, so keeping the old one
    would leave the item pointing at a variant of a different card — the exact inconsistency
    guarded against in the review path. It is cleared and asked about again.
    """
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    card = (
        await session.execute(select(Card).where(Card.tcgdex_id == body.tcgdex_id))
    ).scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=400, detail=f"no such card: {body.tcgdex_id}")

    changed = item.card_id != card.id
    item.card_id = card.id
    # A person looked at it and said so; that is not a probability.
    item.identification_confidence = 1.0
    if changed:
        item.card_variant_id = None

    # Close any identification review: it has just been answered.
    open_review = (
        await session.execute(
            select(Review).where(
                Review.inventory_item_id == item.id,
                Review.category == ReviewCategory.IDENTIFICATION,
                Review.status == ReviewStatus.OPEN,
            )
        )
    ).scalar_one_or_none()
    if open_review is not None:
        open_review.status = ReviewStatus.RESOLVED
        open_review.resolved_at = datetime.now(UTC)
        open_review.resolution = {"resolved_by": "operator", "note": "set from card detail"}

    await session.commit()

    # Re-cut the crop and listing images against the newly-chosen card's art, and ask which
    # variant it is. Choosing the card is only useful if everything downstream follows.
    result: dict = {"ok": True, "sku": sku, "card": card.tcgdex_id, "name": card.name}
    if changed:
        try:
            result["recrop"] = await recrop_front(session, sku, user.id)
            await session.commit()
        except Exception:  # noqa: BLE001 - a failed recrop must not undo the correction
            result["recrop"] = None
        item = (
            await session.execute(
                select(InventoryItem)
                .options(selectinload(InventoryItem.images))
                .where(InventoryItem.sku == sku, InventoryItem.user_id == user.id)
            )
        ).scalar_one_or_none()
        if item is not None:
            result["listing_images"] = await build_for_item(session, item)
            await session.commit()
        result["variant"] = await resolve_variant(session, sku, user.id)
        await session.commit()
    return result


async def _variant_for_finish(session, item, finish: str) -> CardVariant:
    """Find, or create, a variant of this card with the given finish.

    The catalogue is not always right, and promos are where it is least right. TCGdex reports
    xyp-XY48 Meowstic as `normal: true, holo: false` with `variantId: "generated"` — meaning it
    had no variant data and synthesised one — when the card exists only as a holo. Black Star
    Promos generally are holo.

    Since approval requires a variant, a card whose only listed option is wrong cannot be
    finished at all: the operator must either record a finish they can see is false, or abandon
    the card. Neither is acceptable, and the operator holding the card is a better authority
    than a generated catalogue row.

    Locally added variants carry `tcgdex_variant_id = "operator"` so they are distinguishable
    from catalogue data and a later sync cannot silently overwrite the correction.
    """
    if item.card_id is None:
        raise HTTPException(status_code=400, detail="identify the card first")

    existing = (
        (
            await session.execute(
                select(CardVariant).where(CardVariant.card_id == item.card_id)
            )
        )
        .scalars()
        .all()
    )
    wanted = {
        "normal": (True, False, False),
        "holo": (False, True, False),
        "reverse": (False, False, True),
    }[finish]
    for v in existing:
        if (v.is_normal, v.is_holo, v.is_reverse) == wanted and not v.stamp_key:
            return v

    variant = CardVariant(
        card_id=item.card_id,
        tcgdex_variant_id="operator",
        type=finish,
        size="standard",
        stamp_key="",
        is_normal=wanted[0],
        is_holo=wanted[1],
        is_reverse=wanted[2],
    )
    session.add(variant)
    await session.flush()
    return variant


@router.get("/{sku}/variants")
async def list_variants(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """The variants this card was printed in, and which one is currently assigned.

    Offered from the capture strip so a variant can be corrected where it is noticed. Foil is
    the one property that cannot be read from a hand-held photo (D-057), so the operator is the
    sensor — and asking them to leave the scanning screen to fix a one-word field is exactly the
    friction that stops it being fixed at all.
    """
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")
    if item.card_id is None:
        return {"sku": sku, "variants": [], "current": None}

    variants = (
        (
            await session.execute(
                select(CardVariant)
                .where(CardVariant.card_id == item.card_id)
                .order_by(CardVariant.is_normal.desc(), CardVariant.type)
            )
        )
        .scalars()
        .all()
    )
    present = {
        ("normal" if v.is_normal else "holo" if v.is_holo else "reverse" if v.is_reverse else "")
        for v in variants
        if not v.stamp_key
    }
    return {
        "sku": sku,
        "current": str(item.card_variant_id) if item.card_variant_id else None,
        "variants": [
            {
                "id": str(v.id),
                "label": _variant_label(v) or v.type or "variant",
                "source": "operator" if v.tcgdex_variant_id == "operator" else "catalogue",
            }
            for v in variants
        ],
        # Finishes the catalogue does not list. Offered because the catalogue is wrong often
        # enough — especially for promos — that a card can otherwise be unfinishable.
        "addable": [f for f in ("normal", "holo", "reverse") if f not in present],
        # How much the catalogue actually knows about this card's printings.
        #
        # TCGdex marks a variant `variantId: "generated"` when it had no real data and
        # synthesised a row — and that is **26% of the catalogue**: 9,348 of 35,493 variants.
        # Whole sets are affected, promos worst of all (SWSH Black Star Promos 247 cards, XY 215)
        # along with the Pocket sets and the large Sun & Moon sets.
        #
        # A generated row defaults to "normal", which is why XY48 Meowstic — a holo-only promo —
        # offered Normal as its sole option. Saying so turns a silently wrong list into a visibly
        # empty one, and tells the operator to trust the card in their hand instead.
        "catalogue": (
            "generated"
            if variants and all(v.tcgdex_variant_id == "generated" for v in variants)
            else "partial"
            if any(v.tcgdex_variant_id == "generated" for v in variants)
            else "known"
        ),
    }


class VariantIn(BaseModel):
    variant_id: str | None = None
    # An alternative to variant_id: assert a finish the catalogue does not list.
    finish: Literal["normal", "holo", "reverse"] | None = None


@router.post("/{sku}/variant")
async def set_variant(
    sku: str,
    body: VariantIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Assign the variant by hand, and close any variant review that was asking about it."""
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    if body.finish is not None:
        variant = await _variant_for_finish(session, item, body.finish)
    elif body.variant_id:
        variant = await session.get(CardVariant, uuid.UUID(body.variant_id))
        if variant is None or variant.card_id != item.card_id:
            raise HTTPException(status_code=400, detail="that variant is not on this card")
    else:
        raise HTTPException(status_code=400, detail="give a variant_id or a finish")

    item.card_variant_id = variant.id

    # Answering here answers the review too; leaving it open would ask the operator the same
    # question again in a different tab.
    open_review = (
        await session.execute(
            select(Review).where(
                Review.inventory_item_id == item.id,
                Review.category == ReviewCategory.VARIANT,
                Review.status == ReviewStatus.OPEN,
            )
        )
    ).scalar_one_or_none()
    if open_review is not None:
        open_review.status = ReviewStatus.RESOLVED
        open_review.resolved_at = datetime.now(UTC)
        open_review.resolution = {"resolved_by": "operator", "note": "set from capture strip"}

    await session.commit()
    return {"ok": True, "sku": sku, "variant": _variant_label(variant)}


@router.post("/{sku}/reprocess")
async def reprocess(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Re-run the pipeline over both originals. Idempotent; touches only processed images."""
    item = (
        await session.execute(
            select(InventoryItem)
            .options(selectinload(InventoryItem.images))
            .where(InventoryItem.user_id == user.id, InventoryItem.sku == sku)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    originals = [
        i for i in item.images
        if i.kind in (ImageKind.ORIGINAL_FRONT, ImageKind.ORIGINAL_BACK)
    ]
    if not originals:
        raise HTTPException(status_code=409, detail=f"{sku} has no originals to reprocess")

    jobs = [await _enqueue_processing(image.id) for image in originals]

    # Reprocessing rebuilds the crop from edge detection alone, which throws away the
    # reference-derived corners a recognised card already had. Those are strictly better: the
    # front is cropped against its TCGdex art and the back against the card-back reference, and
    # both come out square where the detector's four independently-fitted corners do not.
    #
    # Measured, by reprocessing all twelve stored cards and then re-measuring residual tilt:
    #   after reprocess alone:      fronts sd 1.59 deg, worst 4.37
    #   after reprocess + recognise: fronts sd 0.22 deg, worst 0.81
    # So a bare reprocess silently degrades exactly the cards that were most accurate. Anything
    # already identified gets its reference crop re-asserted.
    recognise = None
    if item.card_id is not None:
        recognise = await _enqueue_recognition(sku, user.id)

    return {
        "sku": sku,
        "queued": len(jobs),
        "jobs": jobs,
        "recognise_job": recognise,
    }


@router.post("/{sku}/detail-shots")
async def cut_corner_shots(
    sku: str,
    # How much of the card each corner shot covers. Smaller closes in on the corner itself.
    fraction: float = Query(0.54, ge=0.15, le=0.9),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Re-cut this card's four corner close-ups.

    Declared above `/{sku}/{side}` on purpose: FastAPI matches in declaration order, so putting
    it after would make "detail-shots" read as a side name and demand a file upload. That has
    bitten this router twice already.

    Lives here rather than on the eBay router, where it used to, because cutting a corner out of
    a photograph is not a selling operation — and the eBay routes are not mounted at all in
    scanner mode, which left the one screen that is *entirely about* corner crops calling an
    endpoint that answered 404.
    """
    from app.services import corner_details

    item = (
        await session.execute(
            select(InventoryItem)
            .options(selectinload(InventoryItem.images))
            .where(InventoryItem.user_id == user.id, InventoryItem.sku == sku.upper())
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    result = await corner_details.build_for_item(session, item, fraction)
    await session.commit()
    return {"sku": item.sku, "fraction": fraction, **result}


async def _attach_extra(session, item, file: UploadFile, source: CaptureSource) -> dict:
    from app.services import extra_shots

    payload = await _read(file)
    try:
        image = await extra_shots.attach(session, item, payload, source=source)
    except extra_shots.ExtraShotError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(item)
    return {
        "sku": item.sku,
        "slot": image.kind.value,
        "url": image_url(image),
        "extras": len(extra_shots.used_slots(item)),
        "remaining": len(EXTRA_KINDS) - len(extra_shots.used_slots(item)),
    }


@router.post("/{sku}/extra")
async def capture_extra(
    sku: str,
    file: UploadFile = File(...),
    source: CaptureSource = Form(CaptureSource.UPLOAD),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Attach an extra shot to a named card.

    Declared above `/{sku}/{side}`, or FastAPI reads "extra" as a side name and this becomes a
    third way to overwrite a card's front. That has bitten this router twice.
    """
    item = (
        await session.execute(
            select(InventoryItem)
            .options(selectinload(InventoryItem.images))
            .where(InventoryItem.user_id == user.id, InventoryItem.sku == sku.upper())
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")
    return await _attach_extra(session, item, file, source)


@router.delete("/{sku}/extra/{number}")
async def delete_extra(
    sku: str,
    number: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Remove one extra shot.

    Nothing is derived from these, so deleting one costs exactly the photograph — which is why
    it is a plain delete and not a soft one. Re-shooting it is a tap.
    """
    from app.services import extra_shots

    item = (
        await session.execute(
            select(InventoryItem)
            .options(selectinload(InventoryItem.images))
            .where(InventoryItem.user_id == user.id, InventoryItem.sku == sku.upper())
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    try:
        slot = extra_shots.slot_from_number(number)
    except extra_shots.ExtraShotError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    removed = await extra_shots.remove(session, item, slot)
    if not removed:
        raise HTTPException(status_code=404, detail=f"{item.sku} has no extra shot {number}")
    await session.commit()
    return {"sku": item.sku, "removed": number}


@router.post("/{sku}/{side}")
async def capture_for_sku(
    sku: str,
    side: Literal["front", "back"],
    file: UploadFile = File(...),
    source: CaptureSource = Form(CaptureSource.UPLOAD),
    replace: bool = Form(False),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Attach a side to a specific card. The escape hatch when auto-pairing is not what you want."""
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    payload = await _read(file)
    kind = ImageKind.ORIGINAL_FRONT if side == "front" else ImageKind.ORIGINAL_BACK
    try:
        image = await attach_image(
            session, item=item, kind=kind, payload=payload, source=source, replace=replace
        )
    except CaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    job_id = await _enqueue_processing(image.id)
    return {"sku": item.sku, "image_id": str(image.id), "side": side, "processing_job": job_id}


# Four segments, so it cannot be matched by `POST /{sku}/{side}` — which it was, silently, as a
# side named "swap-sides". Depth is a sturdier guard than registration order.
@router.post("/{sku}/sides/swap")
async def swap_sides(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Exchange this card's front and back, then process it again.

    For a card shot back-first. Recognition only ever looks at the front, so the symptom is a
    card that simply will not identify and gives no reason — which is a poor way to find out you
    photographed it the wrong way round.

    Both photographs are kept; only their roles change. Everything derived from them is rebuilt,
    because a corner close-up cut from what turned out to be the back is worse than none.
    """
    from app.services.swap_sides import SwapError, reprocess_ids, swap

    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    try:
        result = await swap(session, item)
    except SwapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()

    # After the commit: the worker reads these rows from the database, so enqueueing earlier
    # races it and can process the old bytes.
    jobs = [await _enqueue_processing(image_id) for image_id in reprocess_ids(result)]
    return {**result, "jobs": [j for j in jobs if j]}
