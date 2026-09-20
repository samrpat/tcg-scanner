"""The batch review queue.

Scanning should cost one action per card: put the card down, shoot it. Everything the pipeline
is sure about must land in inventory without anyone confirming it, and everything it is not sure
about has to be visible without anyone going looking. That is the whole design here — one feed,
sorted by whether a human is actually needed.

Successes are returned too, deliberately. An operator who only ever sees failures has no way to
tell "nothing needs me" from "the queue stopped working", so the tab shows recent successes as
well, out of the way.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.enums import ImageKind, InventoryStatus, ReviewCategory, ReviewStatus
from app.models import (
    Card,
    CardSet,
    CardVariant,
    Image,
    InventoryItem,
    Price,
    Review,
    User,
)
from app.routers.capture import current_user, image_url
from app.services.pricing import value_for_variant

router = APIRouter(prefix="/review", tags=["review"])

# Categories that block a card from being listed, in the order an operator should deal with
# them. Identification first: a variant or condition decision made against the wrong card is
# wasted work.
BLOCKING = (ReviewCategory.IDENTIFICATION, ReviewCategory.VARIANT)


class Resolution(BaseModel):
    """How the operator settled a review."""

    # For identification: the chosen card's tcgdex id. For variant: the chosen variant id.
    choice: str | None = None
    # Set when the operator judges the flag wrong and the card is fine as it stands.
    dismiss: bool = False
    note: str | None = None


def _images(item: InventoryItem) -> dict:
    by_kind = {image.kind: image for image in item.images}
    return {
        "front": image_url(
            by_kind.get(ImageKind.LISTING_FRONT) or by_kind.get(ImageKind.PROCESSED_FRONT)
        ),
        "back": image_url(
            by_kind.get(ImageKind.LISTING_BACK) or by_kind.get(ImageKind.PROCESSED_BACK)
        ),
    }


@router.get("/queue")
async def queue(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Everything awaiting a decision, plus recent cards that needed none.

    Both halves come from one call so the tab can render without a second round trip and
    without the two halves disagreeing about what just happened.
    """
    reviews = (
        (
            await session.execute(
                select(Review)
                .where(Review.user_id == user.id, Review.status == ReviewStatus.OPEN)
                .order_by(desc(Review.created_at))
                .limit(limit * 2)
            )
        )
        .scalars()
        .all()
    )
    # Review has no ORM relationship to the item, so fetch the items these point at in one go
    # rather than lazily per review.
    item_ids = {r.inventory_item_id for r in reviews if r.inventory_item_id}
    items = (
        {
            item.id: item
            for item in (
                (
                    await session.execute(
                        select(InventoryItem)
                        .options(selectinload(InventoryItem.images))
                        .where(InventoryItem.id.in_(item_ids))
                    )
                )
                .scalars()
                .all()
            )
        }
        if item_ids
        else {}
    )

    # Group by card: one card with three open reviews is one thing to deal with, not three.
    by_item: dict[uuid.UUID, dict] = {}
    for review in reviews:
        item = items.get(review.inventory_item_id)
        if item is None:
            continue
        entry = by_item.setdefault(
            item.id,
            {
                "sku": item.sku,
                "images": _images(item),
                "card": None,
                "reviews": [],
                "blocking": False,
            },
        )
        # Deferred reviews stay open and still count as work; they just wait in their own list.
        target = "later" if review.deferred_at is not None else "reviews"
        entry.setdefault("later", [])
        entry[target].append(
            {
                "id": str(review.id),
                "category": review.category.value,
                "reason": review.reason,
                "candidates": review.candidates or [],
                "created_at": review.created_at.isoformat(),
            }
        )
        if review.category in BLOCKING:
            entry["blocking"] = True

    # Name the cards that are already identified, so a variant decision has something to show.
    identified = {
        item_id: entry for item_id, entry in by_item.items()
    }
    if identified:
        rows = (
            await session.execute(
                select(InventoryItem.id, Card.tcgdex_id, Card.name, CardSet.name)
                .join(Card, Card.id == InventoryItem.card_id)
                .join(CardSet, CardSet.id == Card.set_id)
                .where(InventoryItem.id.in_(identified.keys()))
            )
        ).all()
        for item_id, tcgdex_id, name, set_name in rows:
            identified[item_id]["card"] = {
                "tcgdex_id": tcgdex_id,
                "name": name,
                "set": set_name,
            }

    # Sorted by SKU alone, deliberately, and NOT by whether the card is blocking.
    #
    # Ranking blocking cards first reorders the list underneath the operator: settling one of a
    # card's reviews drops it below every still-blocking card, so it vanishes from view with its
    # other reviews unanswered. That reads as "answering one wiped the rest" and makes the
    # remaining decisions unreachable without hunting. Position must be stable while a card
    # still needs something; urgency is shown on the card instead.
    grouped = sorted(by_item.values(), key=lambda e: e["sku"])
    # A card belongs in whichever list has something to answer. One with both a live review and
    # a deferred one is live: the operator is already looking at it.
    needs_you = [e for e in grouped if e["reviews"]]
    later = [e for e in grouped if not e["reviews"] and e.get("later")]

    # Recent cards that came through clean. Shown quietly: the operator needs to see the
    # pipeline is alive, not to read them.
    open_item_ids = select(Review.inventory_item_id).where(
        Review.user_id == user.id, Review.status == ReviewStatus.OPEN
    )
    clean_items = (
        (
            await session.execute(
                select(InventoryItem)
                .options(selectinload(InventoryItem.images))
                .where(
                    InventoryItem.user_id == user.id,
                    InventoryItem.card_id.is_not(None),
                    InventoryItem.id.not_in(open_item_ids),
                )
                .order_by(desc(InventoryItem.created_at))
                .limit(24)
            )
        )
        .scalars()
        .all()
    )
    names = dict(
        (
            await session.execute(
                select(InventoryItem.id, Card.name)
                .join(Card, Card.id == InventoryItem.card_id)
                .where(InventoryItem.id.in_([i.id for i in clean_items]))
            )
        ).all()
        if clean_items
        else []
    )

    rescan_items = (
        (
            await session.execute(
                select(InventoryItem)
                .options(selectinload(InventoryItem.images))
                .where(
                    InventoryItem.user_id == user.id,
                    InventoryItem.rescan_requested_at.is_not(None),
                )
                .order_by(InventoryItem.sku)
            )
        )
        .scalars()
        .all()
    )

    return {
        "needs_you": needs_you,
        "later": later,
        "rescan": [
            {"sku": i.sku, "images": _images(i), "requested_at": i.rescan_requested_at.isoformat()}
            for i in rescan_items
        ],
        "clean": [
            {
                "sku": item.sku,
                "name": names.get(item.id),
                "confidence": float(item.identification_confidence or 0),
                "thumbnail": _images(item)["front"],
            }
            for item in clean_items
        ],
        "counts": {
            "needs_you": len(needs_you),
            "later": len(later),
            "rescan": len(rescan_items),
            "blocking": sum(1 for e in needs_you if e["blocking"]),
            "clean": len(clean_items),
        },
    }


@router.post("/{review_id}/resolve")
async def resolve(
    review_id: uuid.UUID,
    resolution: Resolution,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Settle one review, applying the operator's choice to the card."""
    review = (
        await session.execute(
            select(Review).where(Review.id == review_id, Review.user_id == user.id)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="no such review")
    if review.status is not ReviewStatus.OPEN:
        raise HTTPException(status_code=409, detail="review is already settled")

    item = await session.get(InventoryItem, review.inventory_item_id)
    applied = None

    if not resolution.dismiss and resolution.choice and item is not None:
        if review.category is ReviewCategory.IDENTIFICATION:
            card = (
                await session.execute(
                    select(Card).where(Card.tcgdex_id == resolution.choice)
                )
            ).scalar_one_or_none()
            if card is None:
                raise HTTPException(
                    status_code=400, detail=f"no such card: {resolution.choice}"
                )
            item.card_id = card.id
            # A human looked at it, so it is settled — not "confident to 0.87".
            item.identification_confidence = 1.0
            applied = card.tcgdex_id
        elif review.category is ReviewCategory.VARIANT:
            variant = await session.get(CardVariant, uuid.UUID(resolution.choice))
            if variant is None:
                raise HTTPException(
                    status_code=400, detail=f"no such variant: {resolution.choice}"
                )
            # The variant must belong to THIS card. Without the check a stale or mistyped id
            # attaches another card's variant, which is invisible in the UI (the label still
            # reads "Normal") and wrong everywhere it matters: pricing and the listing both key
            # off card_variant_id (D-012).
            if variant.card_id != item.card_id:
                raise HTTPException(
                    status_code=400, detail="that variant is not on this card"
                )
            item.card_variant_id = variant.id
            applied = str(variant.id)

    review.status = ReviewStatus.DISMISSED if resolution.dismiss else ReviewStatus.RESOLVED
    review.resolution = {
        "resolved_by": "operator",
        "choice": resolution.choice,
        "applied": applied,
        "note": resolution.note,
    }
    review.resolved_at = datetime.now(UTC)
    await session.commit()

    return {
        "ok": True,
        "review": str(review.id),
        "status": review.status.value,
        "applied": applied,
    }


# Where to magnify on the rectified front so identification can actually be checked.
#
# Expressed as fractions of the card, derived from the same millimetre regions the OCR uses
# (imaging/ocr.py) — the whole point of rectifying to 88x63 mm is that these are fixed. The UI
# crops the image itself rather than the server cutting new files: it is the same pixels either
# way, and a zoom nobody looks at costs nothing if it is never rendered.
#
# Both bottom corners are offered because the era decides which one carries the number, and the
# set symbol sits beside it in both layouts. Checking those two patches against the named card is
# how an operator catches a reprint that the artwork could never have distinguished.
def _zoom_regions() -> list[dict]:
    from app.imaging.ocr import CARD_H_MM, CARD_W_MM, REGION_BOTTOM_LEFT, REGION_BOTTOM_RIGHT

    def frac(region, pad_mm: float = 1.5) -> dict:
        x0, y0, x1, y1 = region
        return {
            "x": max(0.0, (x0 - pad_mm) / CARD_W_MM),
            "y": max(0.0, (y0 - pad_mm) / CARD_H_MM),
            "w": min(1.0, (x1 - x0 + 2 * pad_mm) / CARD_W_MM),
            "h": min(1.0, (y1 - y0 + 2 * pad_mm) / CARD_H_MM),
        }

    return [
        # Positional labels, not promises: which corner carries the number depends on the era
        # (Sword & Shield and earlier print it bottom-right, Scarlet & Violet bottom-left), so
        # one of these two will show the number and set symbol and the other will show whatever
        # happens to be there. Naming them both "number" made the empty one look like a fault.
        {"label": "Bottom right", **frac(REGION_BOTTOM_RIGHT)},
        {"label": "Bottom left", **frac(REGION_BOTTOM_LEFT)},
    ]


@router.get("/approval")
async def approval_queue(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """The next card awaiting a person's confirmation, with everything needed to judge it.

    One card at a time, oldest first. Everything the operator needs to say yes or no arrives in
    one response — images, what it was identified as and how confidently, the variant, the grade,
    and where to magnify — because a decision that requires opening three other screens is a
    decision that gets rubber-stamped instead of made.
    """
    remaining = (
        await session.execute(
            select(func.count())
            .select_from(InventoryItem)
            .where(
                InventoryItem.user_id == user.id,
                InventoryItem.approved_at.is_(None),
                InventoryItem.approval_deferred_at.is_(None),
                InventoryItem.rescan_requested_at.is_(None),
            )
        )
    ).scalar_one()
    set_aside = (
        await session.execute(
            select(func.count())
            .select_from(InventoryItem)
            .where(
                InventoryItem.user_id == user.id,
                InventoryItem.approved_at.is_(None),
                InventoryItem.approval_deferred_at.is_not(None),
            )
        )
    ).scalar_one()
    awaiting_rescan = (
        await session.execute(
            select(func.count())
            .select_from(InventoryItem)
            .where(
                InventoryItem.user_id == user.id,
                InventoryItem.rescan_requested_at.is_not(None),
            )
        )
    ).scalar_one()
    approved = (
        await session.execute(
            select(func.count())
            .select_from(InventoryItem)
            .where(InventoryItem.user_id == user.id, InventoryItem.approved_at.is_not(None))
        )
    ).scalar_one()

    item = (
        await session.execute(
            select(InventoryItem)
            .options(
                selectinload(InventoryItem.images),
                # The set name is shown, and Card.card_set is lazy — without loading it here the
                # attribute access happens after the await boundary and raises MissingGreenlet.
                selectinload(InventoryItem.card).selectinload(Card.card_set),
                selectinload(InventoryItem.card_variant),
            )
            .where(
                InventoryItem.user_id == user.id,
                InventoryItem.approved_at.is_(None),
                InventoryItem.approval_deferred_at.is_(None),
                InventoryItem.rescan_requested_at.is_(None),
            )
            .order_by(InventoryItem.sku)
            .limit(1)
        )
    ).scalar_one_or_none()

    if item is None:
        return {
            "card": None,
            "remaining": 0,
            "approved": approved,
            "set_aside": set_aside,
            "awaiting_rescan": awaiting_rescan,
            "zoom": _zoom_regions(),
        }

    from app.routers.capture import _image_slots, _variant_label

    by_kind = {i.kind: i for i in item.images}
    open_reviews = (
        (
            await session.execute(
                select(Review).where(
                    Review.inventory_item_id == item.id,
                    Review.status == ReviewStatus.OPEN,
                )
            )
        )
        .scalars()
        .all()
    )

    return {
        "remaining": remaining,
        "approved": approved,
        "set_aside": set_aside,
        "awaiting_rescan": awaiting_rescan,
        "zoom": _zoom_regions(),
        "card": {
            "sku": item.sku,
            "images": _image_slots(by_kind),
            "identified": (
                {
                    "tcgdex_id": item.card.tcgdex_id,
                    "name": item.card.name,
                    "local_id": item.card.local_id,
                    "set": item.card.card_set.name if item.card.card_set else None,
                    # The printed total, so a search can use the full "021/086" form. That is
                    # what single-card listings put in their titles; the bare number matches
                    # every bulk lot in the set.
                    "set_total": (
                        item.card.card_set.card_count_official
                        if item.card.card_set
                        else None
                    ),
                }
                if item.card
                else None
            ),
            "confidence": float(item.identification_confidence or 0),
            "variant": _variant_label(item.card_variant),
            "condition": item.condition.value if item.condition else None,
            "condition_points": item.condition_points,
            # The variant id travels with the card so the price can be edited from this screen
            # without a second lookup.
            "variant_id": (
                str(item.card_variant_id) if item.card_variant_id else None
            ),
            # Condition is passed in so the figure shown is what *this* copy should fetch, not
            # what a Near Mint one would.
            "value": (
                await value_for_variant(
                    session,
                    item.card_variant_id,
                    item.condition.value if item.condition else None,
                )
            ).as_dict(),
            "flags": [
                {"category": r.category.value, "reason": r.reason} for r in open_reviews
            ],
            # What still stands between this card and being approvable, so the button can say
            # so rather than failing when pressed.
            "missing": [
                label
                for label, ok in (
                    ("card not identified", item.card_id is not None),
                    ("no variant chosen", item.card_variant_id is not None),
                )
                if not ok
            ],
        },
    }


@router.post("/approval/{sku}")
async def approve(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Confirm one card and move on.

    Approving also settles any review still open on it: the operator has just looked at the
    finished card with every flag in front of them, which is a strictly better answer than the
    question the flag was asking.
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

    # Approval means the card is finished, so it has to actually be finished.
    #
    # Without this, "Confirm & next" happily approved an ungraded card: the Approved count
    # climbed while Condition graded stayed put, and the result was a card that looks done, is
    # counted as done, and cannot be priced or listed. That is the same silent-gap failure as
    # the missing listing renders (D-086), and it is worse here because the operator created it
    # themselves by pressing the obvious button.
    #
    # Grading is one click for the common case ("Near Mint — no defects"), so requiring it costs
    # almost nothing and makes the Approved number mean something.
    missing = []
    if item.card_id is None:
        missing.append("card not identified")
    if item.card_variant_id is None:
        missing.append("no variant chosen")
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"{sku} is not ready to approve: {', '.join(missing)}",
        )

    now = datetime.now(UTC)
    item.approved_at = now
    if item.status is InventoryStatus.GRADED:
        item.status = InventoryStatus.READY

    open_reviews = (
        (
            await session.execute(
                select(Review).where(
                    Review.inventory_item_id == item.id,
                    Review.status == ReviewStatus.OPEN,
                )
            )
        )
        .scalars()
        .all()
    )
    for review in open_reviews:
        review.status = ReviewStatus.RESOLVED
        review.resolved_at = now
        review.resolution = {"resolved_by": "operator", "note": "approved on review"}

    await session.commit()
    return {"ok": True, "sku": sku, "approved": True, "settled": len(open_reviews)}


@router.post("/approval/{sku}/set-aside")
async def set_aside_card(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Move past a card without approving it, so one hard card cannot stall the queue."""
    item = await _owned_item(session, sku, user)
    item.approval_deferred_at = datetime.now(UTC)
    await session.commit()
    return {"ok": True, "sku": sku, "set_aside": True}


@router.post("/approval/{sku}/needs-rescan")
async def approval_needs_rescan(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Send a card back to the camera from the approval screen."""
    item = await _owned_item(session, sku, user)
    item.rescan_requested_at = datetime.now(UTC)
    await session.commit()
    return {"ok": True, "sku": sku, "rescan": True}


@router.post("/approval/restore")
async def restore_set_aside(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Put every set-aside card back into the queue."""
    result = await session.execute(
        update(InventoryItem)
        .where(
            InventoryItem.user_id == user.id,
            InventoryItem.approval_deferred_at.is_not(None),
        )
        .values(approval_deferred_at=None)
    )
    await session.commit()
    return {"ok": True, "restored": result.rowcount or 0}


async def _owned_item(session: AsyncSession, sku: str, user: User) -> InventoryItem:
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")
    return item


@router.post("/approval/{sku}/unapprove")
async def unapprove(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Put a card back in the queue — the operator spotted something after confirming."""
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")
    item.approved_at = None
    await session.commit()
    return {"ok": True, "sku": sku, "approved": False}


@router.get("/progress")
async def progress(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """How far the collection has got, and what is holding it up.

    A card is not done when it has been photographed. It has to be cropped, identified, given a
    variant, graded, priced and listed, and at two thousand cards the interesting question is
    never "how many did I scan" but "what is the queue at each stage". Counting only open
    reviews answers the second question badly: a card with no variant and no review yet is
    outstanding work that nothing is currently asking about.

    So the stages are derived from the data itself, not from the review queue. Each stage counts
    cards that have cleared it; `outstanding` is what has not.
    """
    total = (
        await session.execute(
            select(func.count()).select_from(InventoryItem).where(
                InventoryItem.user_id == user.id
            )
        )
    ).scalar_one()

    def owned(*where):
        return (
            select(func.count())
            .select_from(InventoryItem)
            .where(InventoryItem.user_id == user.id, *where)
        )

    # An image of each kind, per card, expressed as a correlated existence test.
    def has_image(kind: ImageKind):
        return (
            select(Image.id)
            .where(Image.inventory_item_id == InventoryItem.id, Image.kind == kind)
            .exists()
        )

    both_sides = (
        await session.execute(
            owned(has_image(ImageKind.ORIGINAL_FRONT), has_image(ImageKind.ORIGINAL_BACK))
        )
    ).scalar_one()
    cropped = (
        await session.execute(
            owned(has_image(ImageKind.PROCESSED_FRONT), has_image(ImageKind.PROCESSED_BACK))
        )
    ).scalar_one()
    # Both, not just the front. Checking one meant the bar read "done" while four cards had no
    # listing back at all — a progress bar that overstates is worse than none, because it is
    # the thing the operator uses to decide there is nothing left to chase.
    listed_images = (
        await session.execute(
            owned(has_image(ImageKind.LISTING_FRONT), has_image(ImageKind.LISTING_BACK))
        )
    ).scalar_one()
    identified = (
        await session.execute(owned(InventoryItem.card_id.is_not(None)))
    ).scalar_one()
    with_variant = (
        await session.execute(owned(InventoryItem.card_variant_id.is_not(None)))
    ).scalar_one()
    graded = (
        await session.execute(owned(InventoryItem.condition.is_not(None)))
    ).scalar_one()
    approved_count = (
        await session.execute(owned(InventoryItem.approved_at.is_not(None)))
    ).scalar_one()
    # A card counts as priced when its *variant* carries an observation — prices are per
    # printing, and a holo and a normal of the same card are not worth the same.
    priced = (
        await session.execute(
            select(func.count())
            .select_from(InventoryItem)
            .where(
                InventoryItem.user_id == user.id,
                InventoryItem.card_variant_id.in_(select(Price.card_variant_id).distinct()),
            )
        )
    ).scalar_one()

    stages = [
        {"name": "Both sides captured", "done": both_sides, "phase": 2},
        {"name": "Cropped", "done": cropped, "phase": 2},
        {"name": "Listing images", "done": listed_images, "phase": 3},
        {"name": "Identified", "done": identified, "phase": 3},
        {"name": "Variant assigned", "done": with_variant, "phase": 3},
        {"name": "Condition graded", "done": graded, "phase": 4},
        # The last gate. Nothing is listable until a person has looked at the finished card.
        {"name": "Approved", "done": approved_count, "phase": 4},
        {"name": "Priced", "done": priced, "phase": 5},
    ]
    for stage in stages:
        stage["outstanding"] = total - stage["done"]

    # What is actively being asked about, split from what is merely not done yet.
    review_rows = (
        await session.execute(
            select(Review.category, Review.deferred_at.is_not(None), func.count())
            .where(Review.user_id == user.id, Review.status == ReviewStatus.OPEN)
            .group_by(Review.category, Review.deferred_at.is_not(None))
        )
    ).all()
    blockers: dict[str, dict] = {}
    for category, deferred, count in review_rows:
        entry = blockers.setdefault(category.value, {"open": 0, "later": 0})
        entry["later" if deferred else "open"] += count

    return {
        "total": total,
        "stages": stages,
        "blockers": blockers,
        # The first stage with outstanding work is where attention belongs.
        "next_up": next((s["name"] for s in stages if s["outstanding"] > 0), None),
    }


@router.post("/{review_id}/rescan")
async def request_rescan(
    review_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Flag the card to be photographed again, and take it out of the decision queue.

    An image-quality flag used to offer only "accept it" or "re-run the crop", and neither helps
    when the photograph itself is the problem — no amount of re-cropping fixes a soft or
    badly-lit capture. The honest answer is to pick the card up again, which is a physical task
    for the next time the operator is at the camera, not a decision to make now.

    So the review is set aside rather than resolved: the card appears in the Rescan list until a
    new capture arrives, at which point processing clears the flag on its own.
    """
    review = (
        await session.execute(
            select(Review).where(Review.id == review_id, Review.user_id == user.id)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="no such review")

    item = await session.get(InventoryItem, review.inventory_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="review has no card")

    item.rescan_requested_at = datetime.now(UTC)
    # Out of the decision queue: it is waiting on a camera, not on a judgement.
    review.deferred_at = datetime.now(UTC)
    await session.commit()
    return {"ok": True, "sku": item.sku, "rescan": True}


@router.post("/rescan/{sku}/clear")
async def clear_rescan(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Drop the rescan flag without re-shooting — the operator looked and it is fine."""
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")
    item.rescan_requested_at = None
    await session.commit()
    return {"ok": True, "sku": sku, "rescan": False}


@router.post("/{review_id}/defer")
async def defer(
    review_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Set a review aside without answering it.

    "Later" and "this flag was wrong" are different statements and used to share one button.
    Skipping dismissed the review permanently, so the only way to say "not now" was to say
    "never" — and a card whose variant nobody could judge in the moment was silently written
    off. A deferred review stays open, keeps its place, and is one click from coming back.
    """
    review = (
        await session.execute(
            select(Review).where(Review.id == review_id, Review.user_id == user.id)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="no such review")

    review.deferred_at = datetime.now(UTC)
    await session.commit()
    return {"ok": True, "review": str(review.id), "deferred": True}


@router.post("/{review_id}/resume")
async def resume(
    review_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Bring a deferred review back into the main queue."""
    review = (
        await session.execute(
            select(Review).where(Review.id == review_id, Review.user_id == user.id)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="no such review")

    review.deferred_at = None
    await session.commit()
    return {"ok": True, "review": str(review.id), "deferred": False}


@router.post("/resolve-all-images")
async def resolve_all_images(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Dismiss every open image review at once.

    Image flags are advisory — they say a capture looked soft or low-resolution, not that the
    card is unidentifiable — and they arrive in bulk when a threshold is off. Clearing them one
    at a time is exactly the button-pressing this queue exists to avoid.
    """
    reviews = (
        (
            await session.execute(
                select(Review).where(
                    Review.user_id == user.id,
                    Review.status == ReviewStatus.OPEN,
                    Review.category == ReviewCategory.IMAGE,
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for review in reviews:
        review.status = ReviewStatus.DISMISSED
        review.resolution = {"resolved_by": "operator", "note": "cleared in bulk"}
        review.resolved_at = now
    await session.commit()
    return {"ok": True, "dismissed": len(reviews)}
