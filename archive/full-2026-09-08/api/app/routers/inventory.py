"""The whole collection at once, and the lots it gets sold in.

Every other screen works one card at a time, which is right for judging a card and wrong for
deciding what to do with two thousand of them. This is the view that answers "what do I have,
what is it worth, and what should I actually list" — and, because the answer for most cards is
"not on its own", the lot machinery that follows from it.
"""

import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_session
from app.enums import ImageKind
from app.models import Card, CardSet, InventoryItem, Lot, User
from app.routers.capture import _variant_label, current_user, image_url
from app.services.pricing import value_for_variant

router = APIRouter(prefix="/inventory", tags=["inventory"])


class LotIn(BaseModel):
    name: str
    note: str | None = None


class LotPriceIn(BaseModel):
    asking_price: float | None = None


class LotMembersIn(BaseModel):
    skus: list[str]


def lot_economics(card_values: list[float]) -> dict:
    """What a bundle of cards is worth as one order.

    This is the whole point of a lot. eBay's fixed per-order fee and the postage are paid once
    per *order*, so twenty cards in one envelope pay them once rather than twenty times. Cards
    that are each marginal alone are comfortably worth selling together.

    The suggested price is the sum of the members' eBay estimates. Lots do sell at a discount to
    the sum of their parts — buyers expect one — but by how much is a seller's judgement about
    how fast they want it gone, not something to bake in silently. The figure offered is the
    honest sum; the operator sets what they actually ask.
    """
    total = round(sum(card_values), 2)
    fee = total + settings.ebay_shipping_cost if settings.ebay_buyer_pays_shipping else total
    net = round(
        total - fee * settings.ebay_fee_fraction - settings.ebay_fixed_fee, 2
    )
    if not settings.ebay_buyer_pays_shipping:
        net = round(net - settings.ebay_shipping_cost, 2)
    # What the same cards would net if each were posted separately.
    separately = 0.0
    for value in card_values:
        one_fee = (
            value + settings.ebay_shipping_cost
            if settings.ebay_buyer_pays_shipping
            else value
        )
        one = value - one_fee * settings.ebay_fee_fraction - settings.ebay_fixed_fee
        if not settings.ebay_buyer_pays_shipping:
            one -= settings.ebay_shipping_cost
        separately += one
    return {
        "cards": len(card_values),
        "suggested_price": total,
        "net": net,
        "net_if_sold_separately": round(separately, 2),
        "advantage": round(net - separately, 2),
    }


@router.get("")
async def list_inventory(
    verdict: str | None = Query(None, description="list | marginal | bulk"),
    unlotted: bool = Query(False, description="Only cards not already in a lot"),
    card_set: str | None = Query(None, description="Set name, exact"),
    q: str | None = Query(None, description="Card name or number, substring"),
    min_price: float | None = Query(None, ge=0),
    max_price: float | None = Query(None, ge=0),
    condition: str | None = Query(None, description="NM | LP | MP | HP | DMG"),
    session_id: uuid.UUID | None = Query(None, description="Only this scanning batch"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Every card, with what it is worth and whether it is worth listing alone.

    The filters exist because two thousand cards is not a list anyone reads top to bottom. They
    compose: set plus a price floor is how you find the handful in one set worth listing alone,
    which is the question this screen is actually for.

    Every filter is applied *after* pricing rather than in SQL, because the price a card is
    judged on is condition-adjusted and may come from real eBay comps — neither of which is a
    column. The collection is small enough that this costs nothing and stays correct.
    """
    items = (
        (
            await session.execute(
                select(InventoryItem)
                .options(
                    selectinload(InventoryItem.images),
                    selectinload(InventoryItem.card).selectinload(Card.card_set),
                    selectinload(InventoryItem.card_variant),
                )
                .where(InventoryItem.user_id == user.id)
                .order_by(InventoryItem.sku)
            )
        )
        .scalars()
        .all()
    )

    lot_names = {
        row[0]: row[1]
        for row in (
            await session.execute(select(Lot.id, Lot.name).where(Lot.user_id == user.id))
        ).all()
    }

    rows = []
    for item in items:
        if unlotted and item.lot_id is not None:
            continue
        value = await value_for_variant(
            session, item.card_variant_id, item.condition.value if item.condition else None
        )
        if verdict and value.verdict != verdict:
            continue
        if card_set and (
            not item.card or not item.card.card_set or item.card.card_set.name != card_set
        ):
            continue
        if condition and (item.condition.value if item.condition else None) != condition:
            continue
        if session_id is not None and item.session_id != session_id:
            continue
        estimate = value.ebay_estimate
        if min_price is not None and (estimate is None or estimate < min_price):
            continue
        if max_price is not None and (estimate is None or estimate > max_price):
            continue
        if q:
            needle = q.strip().lower()
            haystack = " ".join(
                x
                for x in [
                    item.sku,
                    item.card.name if item.card else "",
                    item.card.local_id if item.card else "",
                    item.card.card_set.name if item.card and item.card.card_set else "",
                ]
                if x
            ).lower()
            if needle not in haystack:
                continue
        by_kind = {i.kind: i for i in item.images}
        rows.append(
            {
                "sku": item.sku,
                "name": item.card.name if item.card else None,
                "set": item.card.card_set.name if item.card and item.card.card_set else None,
                "number": item.card.local_id if item.card else None,
                "variant": _variant_label(item.card_variant),
                "condition": item.condition.value if item.condition else None,
                "approved": item.approved_at is not None,
                "lot": lot_names.get(item.lot_id),
                "session_id": str(item.session_id) if item.session_id else None,
                "thumbnail": image_url(
                    by_kind.get(ImageKind.LISTING_FRONT)
                    or by_kind.get(ImageKind.PROCESSED_FRONT)
                ),
                "value": value.as_dict(),
            }
        )

    # Totals are what the view is for: a per-card list nobody can add up is just a longer queue.
    priced = [r for r in rows if r["value"]["ebay_estimate"] is not None]
    by_verdict: dict[str, int] = {}
    for r in rows:
        key = r["value"]["verdict"] or "unpriced"
        by_verdict[key] = by_verdict.get(key, 0) + 1

    return {
        "cards": rows,
        "totals": {
            "count": len(rows),
            "priced": len(priced),
            "estimate": round(sum(r["value"]["ebay_estimate"] for r in priced), 2),
            "net_if_all_sold_separately": round(
                sum(r["value"]["net"] for r in priced if r["value"]["net"] is not None), 2
            ),
            "by_verdict": by_verdict,
        },
    }


class TemplateIn(BaseModel):
    """Whatever the operator pasted — a listing URL or a bare item id."""

    reference: str


@router.get("/{sku}/listing")
async def listing_draft(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Everything needed to create this card's eBay listing.

    The listing itself is still created on eBay through "Sell one like this", because that is
    what carries the category and the item specifics across — and for trading cards those
    specifics are what makes a listing findable. This supplies the parts that are tedious by
    hand: a correctly formatted title, the right condition code, the price, and a description.
    """
    from app.conditioning.translate import DEFAULT_TRANSLATIONS
    from app.enums import Marketplace
    from app.services.listing_draft import (
        ListingDraft,
        build_description,
        build_specifics,
        build_title,
        ebay_set_name,
        printed_number,
        sell_one_like_this,
    )

    item = (
        await session.execute(
            select(InventoryItem)
            .options(
                selectinload(InventoryItem.card).selectinload(Card.card_set),
                selectinload(InventoryItem.card_variant),
            )
            .where(InventoryItem.user_id == user.id, InventoryItem.sku == sku)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")
    if item.card is None:
        raise HTTPException(status_code=409, detail="identify the card first")

    card = item.card
    set_name = card.card_set.name if card.card_set else None
    set_total = card.card_set.card_count_official if card.card_set else None
    series_id = card.card_set.series_id if card.card_set else None
    variant = _variant_label(item.card_variant)
    condition = item.condition.value if item.condition else None
    number = printed_number(card.local_id, set_total)

    value = await value_for_variant(session, item.card_variant_id, condition)
    mapped = (
        DEFAULT_TRANSLATIONS.get(Marketplace.EBAY, {}).get(item.condition)
        if item.condition
        else None
    )

    query = " ".join(
        x
        for x in [
            card.name,
            f'"{number}"' if number else "",
            ebay_set_name(set_name, series_id),
            "reverse holo" if variant and "Reverse" in variant else "",
            "-lot -bundle -playset -choose -pick -singles -bulk",
        ]
        if x
    )
    from urllib.parse import quote

    draft = ListingDraft(
        title=build_title(
            card.name,
            card.local_id,
            set_total,
            set_name,
            variant,
            condition,
            card.rarity,
            series_id,
        ),
        description=build_description(
            card.name, number, ebay_set_name(set_name, series_id), variant, condition
        ),
        specifics=build_specifics(
            card.name,
            card.local_id,
            set_total,
            set_name,
            variant,
            rarity=card.rarity,
            hp=card.hp,
            stage=(card.raw or {}).get("stage"),
            illustrator=card.illustrator,
            category=card.category,
            series_id=series_id,
            condition_label=mapped.external_label if mapped else None,
        ),
        condition_label=mapped.external_label if mapped else None,
        condition_code=mapped.external_code if mapped else None,
        price=value.ebay_estimate,
        template_url=sell_one_like_this(item.listing_template_item_id),
        sold_search_url=(
            "https://www.ebay.com/sch/i.html?_nkw="
            + quote(query)
            + "&LH_Sold=1&LH_Complete=1&_sop=13"
        ),
    )

    return {
        "sku": sku,
        "template_item_id": item.listing_template_item_id,
        "draft": draft.as_dict(),
    }


@router.post("/{sku}/listing/template")
async def set_listing_template(
    sku: str,
    body: TemplateIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Remember a past listing to build this card's listing from.

    Accepts a pasted listing URL as readily as a bare item id, because that is what comes off
    the clipboard when someone is looking at a sold listing.
    """
    from app.services.listing_draft import extract_item_id, sell_one_like_this

    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")

    item_id = extract_item_id(body.reference)
    if item_id is None:
        raise HTTPException(
            status_code=400,
            detail="could not find an eBay item id in that — paste the listing URL",
        )
    item.listing_template_item_id = item_id
    await session.commit()
    return {"ok": True, "item_id": item_id, "template_url": sell_one_like_this(item_id)}


@router.get("/export.csv")
async def export_csv(
    lot_id: uuid.UUID | None = Query(None, description="Restrict to one lot"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    """The collection as a spreadsheet.

    Writing listings happens outside this app — in eBay's bulk tools, in a spreadsheet, or by
    hand — and every one of those wants a table. Exporting also means the work is never trapped
    here: a scanning tool that cannot hand its results to anything else is a liability at two
    thousand cards.

    Includes the marketplace condition code, because that is the field an eBay listing actually
    needs and nobody remembers that LP maps to "Excellent" / 400011.
    """
    from app.conditioning.translate import DEFAULT_TRANSLATIONS
    from app.enums import Marketplace

    query = (
        select(InventoryItem)
        .options(
            selectinload(InventoryItem.card).selectinload(Card.card_set),
            selectinload(InventoryItem.card_variant),
        )
        .where(InventoryItem.user_id == user.id)
        .order_by(InventoryItem.sku)
    )
    if lot_id is not None:
        query = query.where(InventoryItem.lot_id == lot_id)
    items = (await session.execute(query)).scalars().all()

    lot_names = {
        row[0]: row[1]
        for row in (
            await session.execute(select(Lot.id, Lot.name).where(Lot.user_id == user.id))
        ).all()
    }
    ebay = DEFAULT_TRANSLATIONS.get(Marketplace.EBAY, {})

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "sku", "card", "set", "number", "tcgdex_id", "variant", "condition",
            "ebay_condition", "ebay_condition_id", "ebay_estimate", "net", "verdict",
            "price_source", "price_age_days", "lot", "approved",
        ]
    )
    for item in items:
        value = await value_for_variant(
            session, item.card_variant_id, item.condition.value if item.condition else None
        )
        mapped = ebay.get(item.condition) if item.condition else None
        writer.writerow(
            [
                item.sku,
                item.card.name if item.card else "",
                item.card.card_set.name if item.card and item.card.card_set else "",
                item.card.local_id if item.card else "",
                item.card.tcgdex_id if item.card else "",
                _variant_label(item.card_variant) or "",
                item.condition.value if item.condition else "",
                mapped.external_label if mapped else "",
                mapped.external_code if mapped else "",
                f"{value.ebay_estimate:.2f}" if value.ebay_estimate is not None else "",
                f"{value.net:.2f}" if value.net is not None else "",
                value.verdict or "",
                value.source or "",
                value.age_days if value.age_days is not None else "",
                lot_names.get(item.lot_id, ""),
                "yes" if item.approved_at else "no",
            ]
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="inventory.csv"'},
    )


@router.get("/lots")
async def list_lots(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> list[dict]:
    lots = (
        (
            await session.execute(
                select(Lot).where(Lot.user_id == user.id).order_by(Lot.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    out = []
    for lot in lots:
        members = (
            (
                await session.execute(
                    select(InventoryItem).where(InventoryItem.lot_id == lot.id)
                )
            )
            .scalars()
            .all()
        )
        values = []
        for m in members:
            v = await value_for_variant(
                session, m.card_variant_id, m.condition.value if m.condition else None
            )
            if v.ebay_estimate is not None:
                values.append(v.ebay_estimate)
        out.append(
            {
                "id": str(lot.id),
                "name": lot.name,
                "note": lot.note,
                "asking_price": float(lot.asking_price) if lot.asking_price else None,
                "listed": lot.listed_at is not None,
                "skus": [m.sku for m in members],
                "economics": lot_economics(values),
            }
        )
    return out


@router.post("/lots")
async def create_lot(
    body: LotIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    lot = Lot(user_id=user.id, name=body.name.strip() or "Untitled lot", note=body.note)
    session.add(lot)
    await session.commit()
    return {"ok": True, "id": str(lot.id), "name": lot.name}


@router.post("/lots/{lot_id}/cards")
async def set_lot_members(
    lot_id: uuid.UUID,
    body: LotMembersIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Add cards to a lot. A card belongs to at most one lot, so this moves rather than copies."""
    lot = await session.get(Lot, lot_id)
    if lot is None or lot.user_id != user.id:
        raise HTTPException(status_code=404, detail="no such lot")

    items = (
        (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.user_id == user.id, InventoryItem.sku.in_(body.skus)
                )
            )
        )
        .scalars()
        .all()
    )
    for item in items:
        item.lot_id = lot.id
    await session.commit()
    return {"ok": True, "lot": str(lot.id), "added": len(items)}


@router.post("/lots/{lot_id}/remove")
async def remove_lot_members(
    lot_id: uuid.UUID,
    body: LotMembersIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    items = (
        (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.user_id == user.id,
                    InventoryItem.lot_id == lot_id,
                    InventoryItem.sku.in_(body.skus),
                )
            )
        )
        .scalars()
        .all()
    )
    for item in items:
        item.lot_id = None
    await session.commit()
    return {"ok": True, "removed": len(items)}


@router.post("/lots/{lot_id}/price")
async def set_lot_price(
    lot_id: uuid.UUID,
    body: LotPriceIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    lot = await session.get(Lot, lot_id)
    if lot is None or lot.user_id != user.id:
        raise HTTPException(status_code=404, detail="no such lot")
    lot.asking_price = body.asking_price
    await session.commit()
    return {"ok": True, "asking_price": body.asking_price}


@router.delete("/lots/{lot_id}")
async def delete_lot(
    lot_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Delete a lot. Its cards are released, never deleted."""
    lot = await session.get(Lot, lot_id)
    if lot is None or lot.user_id != user.id:
        raise HTTPException(status_code=404, detail="no such lot")
    members = (
        (await session.execute(select(InventoryItem).where(InventoryItem.lot_id == lot.id)))
        .scalars()
        .all()
    )
    for m in members:
        m.lot_id = None
    await session.delete(lot)
    await session.commit()
    return {"ok": True, "released": len(members)}


@router.get("/sets")
async def sets_in_collection(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """The sets actually represented in this collection, with counts.

    Offered as a list rather than a text box: a set filter is only useful if it matches exactly,
    and "Chaos Rising" typed by hand will one day be "chaos rising".
    """
    rows = (
        await session.execute(
            select(Card.set_id, func.count(InventoryItem.id))
            .join(InventoryItem, InventoryItem.card_id == Card.id)
            .where(InventoryItem.user_id == user.id)
            .group_by(Card.set_id)
        )
    ).all()
    names = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(CardSet.id, CardSet.name).where(
                    CardSet.id.in_([r[0] for r in rows])
                )
            )
        ).all()
    }
    return {
        "sets": sorted(
            (
                {"name": names.get(set_id, "—"), "count": count}
                for set_id, count in rows
            ),
            key=lambda s: (-s["count"], s["name"]),
        )
    }


class AutoLotIn(BaseModel):
    by: str = "set"          # "set" or "mixed"
    verdict: str | None = "bulk"
    max_per_lot: int = 50
    # Preview by default. Bundling is easy to undo one card at a time and tedious to undo
    # fifty, so the safe direction is to show the split first and commit on a second press.
    dry_run: bool = True


@router.post("/lots/auto")
async def build_lots_automatically(
    body: AutoLotIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Bundle everything not worth listing alone, without doing it by hand.

    Two ways, because they sell differently. **By set** produces "Chaos Rising bulk — 13 cards",
    which a buyer searches for and which is worth more than the sum of its parts to someone
    filling a binder. **Mixed** produces one pile, which is what a reseller buys by weight and is
    the right answer for the long tail of ones and twos that will never make a set lot.

    Cards already in a lot are left alone: this adds, it never reshuffles. Undoing it is the
    existing *Remove from lot*, so a bad split costs one click rather than a rebuild.

    `max_per_lot` exists because postage is not free — an eighty-card lot is a parcel, not an
    envelope, and the economics that make lots worthwhile stop applying.
    """
    if body.by not in {"set", "mixed"}:
        raise HTTPException(status_code=422, detail="by must be 'set' or 'mixed'")
    if not 2 <= body.max_per_lot <= 500:
        raise HTTPException(status_code=422, detail="max_per_lot must be between 2 and 500")

    items = (
        (
            await session.execute(
                select(InventoryItem)
                .options(selectinload(InventoryItem.card).selectinload(Card.card_set))
                .where(
                    InventoryItem.user_id == user.id,
                    InventoryItem.lot_id.is_(None),
                )
                .order_by(InventoryItem.sku)
            )
        )
        .scalars()
        .all()
    )

    # Group first, then create lots, so an empty group never leaves an empty lot behind.
    groups: dict[str, list[InventoryItem]] = {}
    for item in items:
        value = await value_for_variant(
            session, item.card_variant_id, item.condition.value if item.condition else None
        )
        if body.verdict and value.verdict != body.verdict:
            continue
        if body.by == "set":
            key = (
                item.card.card_set.name
                if item.card and item.card.card_set
                else "Unidentified"
            )
        else:
            key = "Mixed"
        groups.setdefault(key, []).append(item)

    created: list[dict] = []
    for name, members in sorted(groups.items()):
        # Split oversized groups rather than making one unpostable lot.
        for index in range(0, len(members), body.max_per_lot):
            chunk = members[index : index + body.max_per_lot]
            if len(chunk) < 2:
                # A "lot" of one card is just a card. Leave it listable on its own.
                continue
            part = index // body.max_per_lot + 1
            suffix = "" if len(members) <= body.max_per_lot else f" ({part})"
            lot_name = f"{name} bulk{suffix} — {len(chunk)} cards"

            if body.dry_run:
                created.append(
                    {
                        "id": None,
                        "name": lot_name,
                        "cards": len(chunk),
                        "skus": [m.sku for m in chunk],
                    }
                )
                continue

            lot = Lot(user_id=user.id, name=lot_name)
            session.add(lot)
            await session.flush()
            for member in chunk:
                member.lot_id = lot.id
            created.append(
                {
                    "id": str(lot.id),
                    "name": lot.name,
                    "cards": len(chunk),
                    "skus": [m.sku for m in chunk],
                }
            )

    if body.dry_run:
        await session.rollback()
    else:
        await session.commit()
    return {
        "created": created,
        "grouped_by": body.by,
        "dry_run": body.dry_run,
        "would_lot": sum(c["cards"] for c in created),
    }


class DeleteIn(BaseModel):
    skus: list[str]


@router.post("/delete")
async def delete_cards(
    body: DeleteIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Delete cards and their photographs.

    A POST rather than a DELETE because it takes a list, and a scan queue needs bulk removal —
    a misfeed produces six bad cards, not one.

    This is genuinely destructive: the images go too, because SKUs are reallocated from
    `max(sku) + 1` and a later card would otherwise inherit a directory of someone else's
    photographs. `make backup` is the safety net; there is no undo here.
    """
    from app.services.purge import purge_images, purge_rows

    if not body.skus:
        raise HTTPException(status_code=422, detail="select some cards first")

    removed = await purge_rows(session, user, body.skus)
    await session.commit()
    # Only now, with the rows definitely gone: file deletion cannot be undone.
    files = purge_images(removed)
    return {"deleted": len(removed), "skus": removed, "files_removed": files}
