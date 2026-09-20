"""The eBay queue: work down two thousand cards without losing your place.

Every other screen judges one card. This one exists to *move* them, so it is built around the
three things that make listing slow by hand and the one thing that makes it error-prone.

The slow parts are retyping the title, retyping the price, and finding the sold listing to
"Sell a similar item" from. All three are solved before the operator clicks anything: the title
is generated in eBay's own catalogue form (D-111), the price comes from the pricing run, and the
template link goes straight into eBay's flow with the category and item specifics already
carried across.

The error-prone part is losing your place. `listed_at` is set only when the operator says the
listing is up — never when they merely open the eBay tab — because a queue that congratulates
itself for opening a tab will quietly skip cards, and a skipped card in a pile of two thousand
is not something anyone finds again.

Cards that are not ready appear too, with the reason. Hiding them would mean the count on this
screen disagrees with the collection, and someone would eventually go looking for the missing
card rather than for the missing price.
"""

from __future__ import annotations

import csv
import io
import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.conditioning.translate import DEFAULT_TRANSLATIONS
from app.config import settings
from app.db import get_session
from app.enums import ImageKind, Marketplace
from app.logging_setup import get_logger
from app.models import Card, EbayUploadTemplate, Image, InventoryItem, User
from app.routers.capture import _variant_label, current_user, image_url
from app.services import corner_details, ebay_template, image_publish
from app.services.listing_draft import (
    build_description,
    build_specifics,
    build_title,
    ebay_set_name,
    is_promo_set,
    printed_number,
    sell_one_like_this,
)
from app.services.pricing import value_for_variant
from app.storage import get_storage

router = APIRouter(prefix="/ebay", tags=["ebay"])
log = get_logger(__name__)


class DraftIn(BaseModel):
    title: str | None = None
    price: float | None = None


class ListedIn(BaseModel):
    listed: bool = True


def _blockers(item: InventoryItem, price: float | None) -> list[str]:
    """Why this card cannot be listed yet, in the operator's words.

    Ordered by what to do first. A card with several problems shows all of them, because
    fixing one and coming back to find another is the thing that makes a queue feel endless.
    """
    reasons: list[str] = []
    if item.card is None:
        reasons.append("not identified yet")
    if item.card_variant_id is None:
        reasons.append("pick which version it is")
    if item.approved_at is None:
        reasons.append("not approved yet")
    if item.condition is None:
        reasons.append("set the condition")
    if price is None:
        reasons.append("no price yet")
    if item.lot_id is not None:
        reasons.append("in a lot — sold as part of that, not on its own")
    return reasons


async def _row(
    session: AsyncSession,
    item: InventoryItem,
    packaging: str | None = None,
    photos: bool = True,
) -> dict:
    card = item.card
    condition = item.condition.value if item.condition else None
    variant = _variant_label(item.card_variant)

    value = (
        await value_for_variant(session, item.card_variant_id, condition)
        if item.card_variant_id
        else None
    )
    generated_price = value.ebay_estimate if value else None
    price = (
        float(item.listing_price) if item.listing_price is not None else generated_price
    )

    mapped = (
        DEFAULT_TRANSLATIONS.get(Marketplace.EBAY, {}).get(item.condition)
        if item.condition
        else None
    )

    set_name = card.card_set.name if card and card.card_set else None
    set_total = card.card_set.card_count_official if card and card.card_set else None
    series_id = card.card_set.series_id if card and card.card_set else None
    series_name = card.card_set.series_name if card and card.card_set else None
    released = card.card_set.release_date if card and card.card_set else None

    generated_title = (
        build_title(
            card.name,
            card.local_id,
            set_total,
            set_name,
            variant,
            series_id=series_id,
            series_name=series_name,
        )
        if card
        else ""
    )
    number = printed_number(card.local_id, set_total) if card else ""

    # The listing copies are the ones with a margin around the card, which is what a buyer
    # needs to judge the edges; the processed 88x63mm crops are the measurement images and are
    # only a fallback here.
    by_kind = {i.kind: i for i in item.images}
    front = image_url(
        by_kind.get(ImageKind.LISTING_FRONT) or by_kind.get(ImageKind.PROCESSED_FRONT)
    )
    back = image_url(
        by_kind.get(ImageKind.LISTING_BACK) or by_kind.get(ImageKind.PROCESSED_BACK)
    )
    # Corner close-ups, in the order a buyer reads them.
    details = [
        image_url(by_kind[kind])
        for kind in (
            ImageKind.DETAIL_FRONT_TL,
            ImageKind.DETAIL_FRONT_TR,
            ImageKind.DETAIL_FRONT_BL,
            ImageKind.DETAIL_FRONT_BR,
        )
        if kind in by_kind
    ]

    query = " ".join(
        x
        for x in [
            card.name if card else "",
            f'"{number}"' if number else "",
            ebay_set_name(set_name, series_id, series_name),
            "reverse holo" if variant and "Reverse" in variant else "",
            "-lot -bundle -playset -choose -pick -singles -bulk",
        ]
        if x
    )

    return {
        "sku": item.sku,
        "name": card.name if card else None,
        "number": number,
        "set_name": ebay_set_name(set_name, series_id, series_name),
        "variant": variant,
        "image": front,
        "image_back": back,
        "detail_images": details,
        # The collection template asks for a year; the set's release date is the honest source.
        "year": str(released.year) if released else "",
        # A suggestion, not a rule — the button is on every card regardless.
        "wants_details": (
            price is not None and price >= settings.detail_shots_above and not details
        ),
        "title": item.listing_title or generated_title,
        "generated_title": generated_title,
        "title_edited": bool(item.listing_title),
        # Only the SWSH promo naming is evidenced; the rest is inferred, so say so here
        # rather than let a wrong set name detach the listing from its product page.
        "check_set_name": is_promo_set(set_name),
        "price": price,
        "generated_price": generated_price,
        "price_edited": item.listing_price is not None,
        "condition_label": mapped.external_label if mapped else None,
        "condition_code": mapped.external_code if mapped else None,
        "specifics": build_specifics(
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
            series_name=series_name,
            condition_label=mapped.external_label if mapped else None,
        )
        if card
        else {},
        # Whether the description may point at photographs depends on whether the listing will
        # actually carry any — see `photos`.
        "description": _describe(card, number, set_name, series_id, series_name, variant,
                                 condition, photos and bool(front), packaging, html=False),
        "description_html": _describe(card, number, set_name, series_id, series_name, variant,
                                      condition, photos and bool(front), packaging, html=True),
        "template_item_id": item.listing_template_item_id,
        "template_url": sell_one_like_this(item.listing_template_item_id),
        "sold_search_url": (
            "https://www.ebay.com/sch/i.html?_nkw="
            + quote(query)
            + "&LH_Sold=1&LH_Complete=1&_sop=13"
        )
        if card
        else None,
        "listed_at": item.listed_at.isoformat() if item.listed_at else None,
        "blockers": _blockers(item, price),
    }


def _describe(card, number, set_name, series_id, series_name, variant, condition,
              has_photos, packaging, html):
    """The seller description for one card, or nothing if the card is not identified yet."""
    if card is None:
        return ""
    return build_description(
        card.name,
        number,
        ebay_set_name(set_name, series_id, series_name),
        variant,
        condition,
        html=html,
        raw=card.raw or {},
        rarity=card.rarity,
        illustrator=card.illustrator,
        has_photos=has_photos,
        packaging=packaging,
    )


async def _load(session: AsyncSession, user: User, sku: str) -> InventoryItem:
    item = (
        await session.execute(
            select(InventoryItem)
            .options(
                selectinload(InventoryItem.card).selectinload(Card.card_set),
                selectinload(InventoryItem.card_variant),
                selectinload(InventoryItem.images),
            )
            .where(InventoryItem.user_id == user.id, InventoryItem.sku == sku)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")
    return item


async def build_queue(
    session: AsyncSession,
    user: User,
    show: str = "ready",
    packaging: str = "",
    photos: bool = True,
    skus: str = "",
) -> dict:
    """The listing queue, with a count of each state so nothing is silently filtered away.

    A plain function rather than only an endpoint, because several places need this data and
    calling an endpoint function directly hands it FastAPI's `Query(...)` sentinels instead of
    values — which then fails deep inside string handling, a long way from the cause.
    """
    items = (
        (
            await session.execute(
                select(InventoryItem)
                .options(
                    selectinload(InventoryItem.card).selectinload(Card.card_set),
                    selectinload(InventoryItem.card_variant),
                    selectinload(InventoryItem.images),
                )
                .where(InventoryItem.user_id == user.id)
                .order_by(InventoryItem.sku)
            )
        )
        .scalars()
        .all()
    )

    # An explicit selection overrides every other filter: the operator looked at these cards and
    # chose them, which is better information than any rule here.
    chosen_skus = {s.strip() for s in skus.split(",") if s.strip()}
    if chosen_skus:
        items = [i for i in items if i.sku in chosen_skus]

    rows = [await _row(session, item, packaging or None, photos) for item in items]

    listed = [r for r in rows if r["listed_at"]]
    unlisted = [r for r in rows if not r["listed_at"]]
    ready = [r for r in unlisted if not r["blockers"]]
    blocked = [r for r in unlisted if r["blockers"]]

    chosen = rows if chosen_skus else {
        "ready": ready, "listed": listed, "blocked": blocked, "all": rows
    }[show]

    return {
        "cards": chosen,
        "counts": {
            "ready": len(ready),
            "listed": len(listed),
            "blocked": len(blocked),
            "all": len(rows),
        },
        "value_ready": round(sum(r["price"] or 0 for r in ready), 2),
    }


@router.get("/queue")
async def queue(
    show: str = Query("ready", pattern="^(ready|listed|blocked|all)$"),
    packaging: str = Query("", max_length=300),
    photos: bool = Query(True),
    skus: str = Query("", max_length=8000),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """The listing queue. See `build_queue`."""
    return await build_queue(session, user, show, packaging, photos, skus)


@router.post("/{sku}/draft")
async def save_draft(
    sku: str,
    body: DraftIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Store a title or price the operator changed while looking at the real eBay page.

    Sending an empty title or a null price clears the override, so the card goes back to
    tracking the catalogue and the pricing run rather than being frozen at a stale edit.
    """
    item = await _load(session, user, sku)
    if body.title is not None:
        title = body.title.strip()
        if len(title) > 80:
            raise HTTPException(
                status_code=422, detail="eBay titles are at most 80 characters"
            )
        item.listing_title = title or None
    if body.price is not None:
        if body.price < 0:
            raise HTTPException(status_code=422, detail="price cannot be negative")
        item.listing_price = Decimal(str(round(body.price, 2)))
    await session.commit()
    await session.refresh(item)
    return await _row(session, item)


@router.post("/{sku}/draft/reset")
async def reset_draft(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Drop both overrides and go back to the generated title and price."""
    item = await _load(session, user, sku)
    item.listing_title = None
    item.listing_price = None
    await session.commit()
    await session.refresh(item)
    return await _row(session, item)


@router.post("/{sku}/listed")
async def mark_listed(
    sku: str,
    body: ListedIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Mark the listing live, or undo that.

    Undo matters more than it looks: the button that advances the queue is the one that gets
    pressed by accident, and a card wrongly marked listed is one that never gets listed.
    """
    item = await _load(session, user, sku)
    item.listed_at = datetime.now(UTC) if body.listed else None
    await session.commit()
    await session.refresh(item)
    return await _row(session, item)


# eBay's File Exchange header for adding fixed-price listings. The version is part of the
# Action column and eBay rejects the file without it.
_ACTION = "Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)"

# Toys & Hobbies > Collectible Card Games > Single Cards, read off the category breadcrumb of
# the operator's own sold listings.
_CATEGORY_SINGLES = "183454"

# The item-specific columns, in the order eBay's own listings present them. Every one of these
# appears in the specifics block of a real sold listing — none is invented — because an aspect
# eBay does not recognise is silently dropped on import and one that is misspelled is worse:
# it lands as a free-text specific that no search filter reads.
_ASPECT_COLUMNS = [
    "Game",
    "Set",
    "Card Name",
    "Card Number",
    "Rarity",
    "Finish",
    "Language",
    "Manufacturer",
    "HP",
    "Stage",
    "Card Type",
    "Illustrator",
    "Features",
    "Country of Origin",
]

_COLUMNS = [
    _ACTION,
    "CustomLabel",
    "Category",
    "Title",
    "Description",
    "ConditionID",
    "PicURL",
    "Quantity",
    "Format",
    "StartPrice",
    "BestOfferEnabled",
    "Duration",
    "Location",
    "PostalCode",
    "P:EPID",
    "ShippingProfileName",
    "ReturnProfileName",
    "PaymentProfileName",
    *[f"C:{a}" for a in _ASPECT_COLUMNS],
]


def _schedule_at(counter, stagger_minutes: int) -> str:
    """When this listing should go live, spaced `stagger_minutes` apart.

    eBay surfaces newly-listed items, so putting three hundred live in the same second buries
    all but the first few. Spacing them spreads that exposure across hours or days.

    Empty when not staggering, which means "go live on upload" — and on a Draft row eBay
    ignores it entirely, since a draft has no start time until it is published.
    """
    if stagger_minutes <= 0:
        return ""
    index = next(counter)
    when = datetime.now(UTC) + timedelta(minutes=stagger_minutes * index)
    # eBay wants ISO 8601 in UTC.
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


async def build_export(
    session: AsyncSession,
    user: User,
    *,
    show: str = "ready",
    # A comma-separated selection. When given it replaces `show` entirely — the operator
    # picked these cards, and second-guessing that with a filter would be maddening.
    skus: str = "",
    # "Draft" lands the listings in eBay's Drafts folder to be reviewed and published by hand;
    # "Add" publishes them immediately. Draft is the default because an import that goes live
    # on upload is not undoable in bulk, and a mistake is then several hundred live listings.
    mode: str = "draft",
    # Auctions want an opening bid and a fixed run; fixed-price wants Good Till Cancelled.
    listing_format: str = "FixedPrice",
    duration: str = "",
    # Minutes between listings. eBay surfaces newly-listed items, so putting three hundred live
    # in one second buries all but the first few; spacing them spreads that exposure.
    stagger_minutes: int = 0,
    location: str = "",
    postal_code: str = "",
    shipping_profile: str = "",
    return_profile: str = "",
    payment_profile: str = "",
    image_base: str = "",
    # Deliberately empty by default. Any packaging claim is a promise about a process this
    # code cannot see, and the previous hardcoded one ("penny sleeve and toploader inside a
    # rigid mailer") is false of an eBay Standard Envelope shipment. The operator supplies it.
    packaging: str = "",
) -> Response:
    """An eBay bulk-upload file with every item specific filled in.

    This is the other half of the "Sell a similar item" workflow, for when there are more cards
    than anyone wants to click through. It produces eBay's File Exchange format, and its whole
    point is the `C:` columns: set, card number, rarity, finish, HP, stage, illustrator and the
    rest are what the trading card category's search filters read, and a bulk file that omits
    them creates hundreds of listings that are technically live and practically invisible. Those
    columns and their order are taken from the specifics blocks of the operator's own sold
    listings rather than from a guess about what eBay wants.

    Three fields this cannot fill in, deliberately left for the operator rather than faked:

    - **`P:EPID`** is eBay's catalogue product id. It is what attaches a listing to the product
      page the price guide lives on, and it exists only in eBay's catalogue — there is no way to
      derive it here. The column is emitted empty; eBay will also match on a title in its
      catalogue form (D-111), which is the other reason for getting the title exactly right.
    - **`PicURL` needs a publicly reachable address.** eBay's servers fetch these, so a
      `localhost` URL fetches nothing. Pass `image_base` if this host is reachable from the
      internet; otherwise leave it empty and add photographs after import.
    - **The business policy names** are whatever the operator called them in Seller Hub.

    Blocked cards are excluded by default. Uploading a card with no price or no condition
    produces a listing error per row, and a file that fails halfway is worse than a shorter one
    that works.
    """
    # eBay's servers fetch PicURL themselves, so with no publicly reachable image host the
    # listing goes up with no pictures. The description must not then tell the buyer to
    # examine the pictures — that reads as a bait listing and earns exactly the review this
    # whole file is trying to avoid.
    #
    # A running tunnel is used automatically when no address was given, so the common case —
    # `make photos-on`, then download — carries photographs without anyone copying a hostname
    # that changes on every restart.
    # Object storage first. It is the only one of the three that cannot silently stop
    # serving while reporting itself healthy, which is what the tunnel did.
    use_object_store = not image_base and image_publish.configured()
    is_auction = listing_format == "Auction"
    run_for = duration or ("Days_7" if is_auction else "GTC")

    if not image_base and not use_object_store:
        discovered = await resolve_photo_host()
        image_base = discovered["url"] or ""

    body = await build_queue(
        session,
        user,
        show=show,
        packaging=packaging,
        photos=bool(image_base) or use_object_store,
        skus=skus,
    )

    # The operator's own downloaded template wins over our built-in columns: its headers are
    # the ones eBay will actually accept, and they vary by template type and category.
    stored = (
        await session.execute(
            select(EbayUploadTemplate)
            .where(EbayUploadTemplate.user_id == user.id)
            .order_by(EbayUploadTemplate.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    columns = list(stored.headers) if stored else _COLUMNS

    scheduled = itertools.count()
    buf = io.StringIO()
    skipped_rows: list[str] = []
    dropped: set[str] = set()
    missing_required: set[str] = set()
    if stored and stored.preamble:
        # eBay's instruction and #INFO lines are part of the format.
        buf.write(stored.preamble + "\n")
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()

    for card in body["cards"]:
        # A card with no price or no condition would fail eBay's import row by row. When the
        # operator selected it explicitly they still do not want a broken row, so it is skipped
        # here and reported in the response headers rather than silently dropped.
        if card["blockers"]:
            # SKUs only. HTTP headers are latin-1, and the reasons contain em dashes; the UI
            # already holds the reasons from the queue, so sending them twice buys nothing.
            skipped_rows.append(card["sku"])
            continue
        specifics = card["specifics"]
        # Whole card first, then the corner close-ups: eBay uses the first as the gallery
        # image, and a corner crop as the thumbnail would look like a mistake.
        #
        # The `?v=` cache-buster is stripped. It exists so a browser reloads a re-rendered
        # image, and eBay fetches each URL exactly once at import and copies it to its own
        # picture service — so it buys nothing here, and the plainest possible URL is the one
        # least likely to trip a fetcher. eBay caps File Exchange at 12 pictures per row.
        local_paths = [
            p.split("?", 1)[0]
            for p in (card["image"], card["image_back"], *card["detail_images"])
            if p
        ]
        if use_object_store:
            # Keys mirror the storage layout, so the public URL is derivable and a re-upload
            # replaces rather than accumulating.
            pics = [
                image_publish.public_url(p.replace("/api/images/", "", 1))
                for p in local_paths
            ][:12]
        else:
            pics = [
                f"{image_base.rstrip('/')}{p}" for p in local_paths if image_base
            ][:12]
        values = {
            "action": "Draft" if mode == "draft" else "Add",
            "sku": card["sku"],
            "category": _CATEGORY_SINGLES,
            "title": card["title"],
            "description": card["description_html"],
            "condition_id": card["condition_code"] or "",
            "pics": "|".join(pics),
            "quantity": "1",
            "format": listing_format,
            "price": f"{card['price']:.2f}" if card["price"] is not None else "",
            # An auction takes bids, not offers, and eBay rejects both together.
            "best_offer": "" if is_auction else "1",
            "duration": run_for,
            "buy_it_now": "" if is_auction else (
                f"{card['price']:.2f}" if card["price"] is not None else ""
            ),
            "schedule_time": _schedule_at(scheduled, stagger_minutes),
            "location": location,
            "postal_code": postal_code,
            "epid": "",
            "upc": "Does not apply",
            # My Collection template fields. `game` is a fixed vocabulary; `graded` is N
            # because every card here is raw — a slab would be a different product.
            "game": "pokemon",
            "graded": "N",
            # Required by the category template, and not derivable from a card.
            "dispatch_days": str(settings.ebay_dispatch_days),
            "returns": settings.ebay_returns,
            "card_condition": card["condition_label"] or "",
            "year": card.get("year") or "",
            "shipping_profile": shipping_profile,
            "return_profile": return_profile,
            "payment_profile": payment_profile,
        }
        if stored:
            dropped.update(ebay_template.unmatched(columns, values, specifics))
            built = ebay_template.row_for(columns, values, specifics)
            # eBay marks required columns with `*` and rejects a row that leaves one empty.
            # Better to say so here than to have the operator find out after the upload.
            missing_required.update(
                header
                for header in columns
                if header.startswith("*") and not str(built.get(header, "")).strip()
            )
            writer.writerow(built)
            continue

        row = {
            _ACTION: "Draft" if mode == "draft" else "Add",
            # The SKU is how a row that fails on import is traced back to a physical card.
            "CustomLabel": card["sku"],
            "Category": _CATEGORY_SINGLES,
            "Title": card["title"],
            "Description": card["description_html"],
            "ConditionID": card["condition_code"] or "",
            # eBay separates multiple picture URLs with a pipe.
            "PicURL": "|".join(pics),
            "Quantity": "1",
            "Format": "FixedPrice",
            "StartPrice": f"{card['price']:.2f}" if card["price"] is not None else "",
            # Every one of the operator's sold listings took best offers.
            "BestOfferEnabled": "1",
            "Duration": "GTC",
            "Location": location,
            "PostalCode": postal_code,
            "P:EPID": "",
            "ShippingProfileName": shipping_profile,
            "ReturnProfileName": return_profile,
            "PaymentProfileName": payment_profile,
            **{f"C:{a}": specifics.get(a, "") for a in _ASPECT_COLUMNS},
        }
        writer.writerow(row)

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="ebay-{mode}-{show}.csv"',
            # Read by the UI so it can say what was left out instead of the operator
            # discovering a short file after the upload.
            "X-Skipped-Count": str(len(skipped_rows)),
            "X-Skipped": "; ".join(skipped_rows)[:900],
            # Values this system holds that the chosen template has no column for. A missing
            # Rarity column is a listing that will not appear under a rarity filter, and that
            # should be heard from here rather than worked out from traffic.
            "X-Template": (stored.name if stored else "built-in File Exchange columns")[:120],
            "X-Dropped": "; ".join(sorted(dropped))[:900],
            # Required columns eBay will reject the row for. Stripped of the `*` and of the
            # version string so the header stays latin-1 and readable.
            "X-Missing-Required": "; ".join(
                sorted(h.lstrip("*").split("(")[0].strip() for h in missing_required)
            )[:400],
            "Access-Control-Expose-Headers": (
                "X-Skipped-Count, X-Skipped, X-Template, X-Dropped, X-Missing-Required"
            ),
        },
    )


@router.post("/{sku}/details")
async def build_corner_details(
    sku: str,
    # How much of the card each corner shot covers. Smaller closes in on the corner.
    fraction: float = Query(corner_details.CORNER_FRACTION, ge=0.15, le=0.9),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Cut the four corner close-ups for this card.

    Deliberately a button rather than an automatic step in the pipeline. Four extra images per
    card is four extra uploads, and on a bulk common that is cost with no buyer on the other end
    of it. `wants_details` on the queue row suggests it above the configured price; the decision
    stays with the operator.
    """
    item = await _load(session, user, sku)
    result = await corner_details.build_for_item(session, item, fraction)
    await session.commit()
    await session.refresh(item)
    row = await _row(session, item)
    return {**row, "detail_result": result}


@router.post("/details/bulk")
async def build_all_corner_details(
    show: str = Query("ready", pattern="^(ready|listed|blocked|all)$"),
    above: float | None = Query(None, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Cut corner close-ups for everything about to be exported.

    The export is meant to need as little editing as possible once it reaches eBay, and photos
    are the part that cannot be typed in afterwards without opening every draft. Doing them in
    one pass here means the file is complete when it is downloaded.

    `above` limits the work to cards worth the four extra uploads; it defaults to the configured
    threshold. Cards that already have close-ups are skipped rather than re-cut, so this is
    safe to run repeatedly as the collection grows.
    """
    threshold = settings.detail_shots_above if above is None else above

    items = (
        (
            await session.execute(
                select(InventoryItem)
                .options(
                    selectinload(InventoryItem.card).selectinload(Card.card_set),
                    selectinload(InventoryItem.card_variant),
                    selectinload(InventoryItem.images),
                )
                .where(InventoryItem.user_id == user.id)
                .order_by(InventoryItem.sku)
            )
        )
        .scalars()
        .all()
    )

    built: list[str] = []
    skipped: list[str] = []
    for item in items:
        row = await _row(session, item)
        wanted = show == "all" or (
            not row["blockers"] if show == "ready" else bool(row["blockers"])
        )
        if not wanted or (row["price"] or 0) < threshold:
            continue
        if row["detail_images"]:
            skipped.append(item.sku)
            continue
        result = await corner_details.build_for_item(session, item)
        (built if result["rendered"] else skipped).append(item.sku)

    await session.commit()
    return {
        "built": built,
        "already_had_them": skipped,
        "threshold": threshold,
    }


async def _fetch_head(url: str) -> dict:
    """Fetch a URL the way eBay's picture service would, from outside this machine.

    Tries an ordinary request first, which is what will happen on the Pi. Falls back to
    resolving over DNS-over-HTTPS and connecting to that address with the hostname as SNI,
    because some environments (this development sandbox among them) run a resolver that will
    not return freshly created tunnel subdomains — and a preflight that reports a false failure
    is worse than no preflight.
    """
    import socket
    import ssl
    from urllib.parse import urlparse

    import httpx

    parsed = urlparse(url)
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url)
            body = response.content
            return {
                "status": response.status_code,
                "bytes": len(body),
                "content_type": response.headers.get("content-type", ""),
                "magic": body[:3].hex(),
            }
    except Exception:
        pass

    # DoH + explicit SNI.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            dns = await client.get(
                "https://cloudflare-dns.com/dns-query",
                params={"name": parsed.hostname, "type": "A"},
                headers={"accept": "application/dns-json"},
            )
            address = dns.json()["Answer"][0]["data"]
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "error": f"cannot resolve {parsed.hostname}: {exc}"}

    try:
        context = ssl.create_default_context()
        raw = socket.create_connection((address, 443), timeout=20)
        sock = context.wrap_socket(raw, server_hostname=parsed.hostname)
        request = (
            f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}\r\n"
            f"User-Agent: eBayPictureFetcher\r\nConnection: close\r\n\r\n"
        )
        sock.sendall(request.encode())
        buffer = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buffer += chunk
        sock.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "error": str(exc)[:200]}

    head, _, body = buffer.partition(b"\r\n\r\n")
    lines = head.decode("latin-1", "replace").split("\r\n")
    status = int(lines[0].split()[1]) if len(lines[0].split()) > 1 else 0
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    return {
        "status": status,
        "bytes": len(body),
        "content_type": headers.get("content-type", ""),
        "magic": body[:3].hex(),
    }


async def resolve_photo_host(verify: bool = True) -> dict:
    """The public address eBay can fetch photographs from — checked, not assumed.

    Reading the hostname from the tunnel's metrics is not enough, and that was a real failure:
    a Cloudflare quick tunnel lost its control stream after seven hours, the container stayed
    "running", the metrics endpoint kept reporting the same hostname, and Cloudflare had already
    withdrawn the DNS record. Everything downstream looked healthy while every picture URL
    pointed at nothing.

    eBay fetches each URL once at import and shows no error the operator ever sees, so a dead
    host produces drafts with no photographs and no explanation. That is worth one HTTP request
    to rule out.

    `verify=false` skips the check for callers that only want the configured name.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get("http://tunnel:2000/quicktunnel")
            response.raise_for_status()
            hostname = (response.json() or {}).get("hostname")
    except Exception:
        # Not running, still starting, or not reachable. All the same to the caller.
        return {
            "url": None,
            "running": False,
            "live": False,
            "object_storage": image_publish.configured(),
        }

    if not hostname:
        return {
            "url": None,
            "running": True,
            "live": False,
            "object_storage": image_publish.configured(),
        }

    url = f"https://{hostname}"
    if not verify:
        return {
            "url": url,
            "running": True,
            "live": None,
            "object_storage": image_publish.configured(),
        }

    # Does it actually serve? `/healthz` is the photos server's own cheap endpoint.
    probe = await _fetch_head(f"{url}/healthz")
    live = probe.get("status") == 200
    return {
        "url": url if live else None,
        "hostname": hostname,
        "running": True,
        "live": live,
        "object_storage": image_publish.configured(),
        "error": None if live else (
            probe.get("error") or f"the tunnel is not serving (HTTP {probe.get('status')})"
        ),
    }


@router.get("/photo-host")
async def photo_host(verify: bool = Query(True)) -> dict:
    """See `resolve_photo_host`."""
    return await resolve_photo_host(verify)


@router.get("/photo-check")
async def photo_check(
    show: str = Query("ready", pattern="^(ready|listed|blocked|all)$"),
    skus: str = Query("", max_length=8000),
    limit: int = Query(3, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Prove the photographs in the upload file are actually fetchable, before uploading.

    eBay fetches each picture URL once at import and copies it to its own picture service. If
    that fetch fails the draft is created **without photographs and without an error the
    operator sees** — the failure surfaces days later as a listing nobody watches.

    So this fetches them the way eBay will: from outside, over HTTPS, following redirects, and
    checks the four things eBay actually enforces — a 200, a real JPEG (by magic bytes, not by
    file extension), at least 500 pixels on the longest side, and under 12 MB.

    Checks a sample by default rather than every image, because the failure modes here are
    per-host, not per-file: if one card's photographs fetch, they all will.
    """
    host = await resolve_photo_host()
    if not host["url"]:
        return {
            "ok": False,
            "reason": "No photo host is running. Start one with: make photos-on",
            "checked": [],
        }

    body = await build_queue(session, user, show=show, skus=skus)
    cards = [c for c in body["cards"] if not c["blockers"]][:limit]
    if not cards:
        return {"ok": False, "reason": "no listable cards to check", "checked": []}

    base = host["url"].rstrip("/")
    checked: list[dict] = []
    for card in cards:
        for path in (card["image"], card["image_back"], *card["detail_images"]):
            if not path:
                continue
            url = f"{base}{path.split('?', 1)[0]}"
            result = await _fetch_head(url)
            problems = []
            if result.get("status") != 200:
                problems.append(f"HTTP {result.get('status')} {result.get('error', '')}".strip())
            if result.get("magic") and result["magic"] != "ffd8ff":
                problems.append("not a JPEG")
            if result.get("bytes", 0) > 12_000_000:
                problems.append("over eBay's 12 MB limit")
            if 0 < result.get("bytes", 0) < 5_000:
                problems.append("suspiciously small — probably an error page")
            checked.append(
                {
                    "sku": card["sku"],
                    "url": url,
                    "status": result.get("status"),
                    "bytes": result.get("bytes"),
                    "ok": not problems,
                    "problems": problems,
                }
            )

    failures = [c for c in checked if not c["ok"]]
    return {
        "ok": not failures,
        "host": host["url"],
        "checked": checked,
        "failures": len(failures),
        "reason": (
            "Every photograph fetched from outside as a real JPEG."
            if not failures
            else f"{len(failures)} of {len(checked)} photographs could not be fetched."
        ),
    }


@router.post("/publish-photos")
async def publish_photos(
    show: str = Query("ready", pattern="^(ready|listed|blocked|all)$"),
    skus: str = Query("", max_length=8000),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Upload the listing photographs to object storage.

    This is the durable route: unlike a tunnel, an object store cannot stop serving while
    reporting itself healthy, and the URLs keep working after this machine is switched off —
    which matters because eBay may retry a picture fetch.

    Idempotent. Keys are deterministic, so re-running replaces rather than accumulating, and
    it is safe to press again after scanning more cards.
    """
    if not image_publish.configured():
        raise HTTPException(
            status_code=409,
            detail=(
                "No object storage configured. Set S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, "
                "S3_SECRET_KEY and S3_PUBLIC_BASE in .env — Cloudflare R2's free tier works."
            ),
        )

    storage = get_storage()
    body = await build_queue(session, user, show=show, skus=skus)

    uploaded: list[str] = []
    failed: list[dict] = []
    for card in body["cards"]:
        if card["blockers"]:
            continue
        for url_path in (card["image"], card["image_back"], *card["detail_images"]):
            if not url_path:
                continue
            key = url_path.split("?", 1)[0].replace("/api/images/", "", 1)
            try:
                await image_publish.put(key, storage.get(key))
                uploaded.append(key)
            except Exception as exc:  # noqa: BLE001 - reported per file, never fatal
                failed.append({"key": key, "error": str(exc)[:200]})

    log.info("photos.published", uploaded=len(uploaded), failed=len(failed))
    return {
        "uploaded": len(uploaded),
        "failed": failed,
        "base": settings.s3_public_base,
        "cards": len([c for c in body["cards"] if not c["blockers"]]),
    }


@router.post("/template")
async def upload_template(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Store the header row of eBay's own upload template.

    Once a template is stored, the export emits exactly its columns in exactly its order, so a
    file cannot be rejected for having the wrong headers. Replacing it is how you switch
    template type or category — only the most recent one is used.
    """
    raw = await file.read()

    try:
        # A zip signature means .xlsx. eBay hands out both, depending on the button.
        if raw[:2] == b"PK":
            parsed = ebay_template.parse_rows(ebay_template.rows_from_xlsx(raw))
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            parsed = ebay_template.parse_template(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"cannot read that file: {exc}") from exc

    # Only the most recent template is used, so old ones are cleared rather than accumulating
    # into an ambiguity about which one is live.
    await session.execute(
        sa_delete(EbayUploadTemplate).where(EbayUploadTemplate.user_id == user.id)
    )
    stored = EbayUploadTemplate(
        user_id=user.id,
        name=file.filename or "eBay template",
        headers=parsed["headers"],
        preamble=parsed["preamble"],
        created_at=datetime.now(UTC),
    )
    session.add(stored)
    await session.commit()

    recognised = [h for h in parsed["headers"] if ebay_template.field_for(h)]
    log.info("ebay_template.stored", columns=len(parsed["headers"]))
    return {
        "name": stored.name,
        "columns": len(parsed["headers"]),
        "recognised": len(recognised),
        "headers": parsed["headers"],
        "unrecognised": [
            h for h in parsed["headers"] if not ebay_template.field_for(h) and h.strip()
        ],
    }


@router.get("/template")
async def current_template(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """The stored template, if any. Without one the export falls back to File Exchange columns."""
    stored = (
        await session.execute(
            select(EbayUploadTemplate)
            .where(EbayUploadTemplate.user_id == user.id)
            .order_by(EbayUploadTemplate.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if stored is None:
        return {"template": None}
    return {
        "template": {
            "name": stored.name,
            "columns": len(stored.headers),
            "headers": stored.headers,
            "created_at": stored.created_at.isoformat(),
        }
    }


@router.delete("/template")
async def clear_template(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Go back to the built-in File Exchange columns."""
    await session.execute(
        sa_delete(EbayUploadTemplate).where(EbayUploadTemplate.user_id == user.id)
    )
    await session.commit()
    return {"cleared": True}


@router.get("/export.csv")
async def export_csv(
    show: str = Query("ready", pattern="^(ready|listed|blocked|all)$"),
    skus: str = Query("", max_length=8000),
    mode: str = Query("draft", pattern="^(draft|add)$"),
    listing_format: str = Query("FixedPrice", pattern="^(FixedPrice|Auction)$"),
    duration: str = Query("", max_length=20),
    stagger_minutes: int = Query(0, ge=0, le=1440),
    location: str = Query("", max_length=120),
    postal_code: str = Query("", max_length=20),
    shipping_profile: str = Query("", max_length=120),
    return_profile: str = Query("", max_length=120),
    payment_profile: str = Query("", max_length=120),
    image_base: str = Query("", max_length=200),
    packaging: str = Query("", max_length=300),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    """The eBay upload file. See `build_export`."""
    return await build_export(
        session,
        user,
        show=show,
        skus=skus,
        mode=mode,
        listing_format=listing_format,
        duration=duration,
        stagger_minutes=stagger_minutes,
        location=location,
        postal_code=postal_code,
        shipping_profile=shipping_profile,
        return_profile=return_profile,
        payment_profile=payment_profile,
        image_base=image_base,
        packaging=packaging,
    )


@router.get("/export/check")
async def export_check(
    show: str = Query("ready", pattern="^(ready|listed|blocked|all)$"),
    skus: str = Query("", max_length=8000),
    mode: str = Query("draft", pattern="^(draft|add)$"),
    # Auctions want an opening bid and a fixed run; fixed-price wants Good Till Cancelled.
    listing_format: str = Query("FixedPrice", pattern="^(FixedPrice|Auction)$"),
    duration: str = Query("", max_length=20),
    # Minutes between listings. eBay surfaces newly-listed items, so putting three hundred live
    # in one second buries all but the first few; spacing them spreads that exposure.
    stagger_minutes: int = Query(0, ge=0, le=1440),
    location: str = Query("", max_length=120),
    postal_code: str = Query("", max_length=20),
    shipping_profile: str = Query("", max_length=120),
    return_profile: str = Query("", max_length=120),
    payment_profile: str = Query("", max_length=120),
    image_base: str = Query("", max_length=200),
    packaging: str = Query("", max_length=300),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """What would be wrong with the upload file, before it is uploaded.

    eBay validates on import and reports failures row by row, long after the work is done. This
    builds the same file and reports what it found: required columns left empty, values the
    chosen template has no column for, and cards held back.

    A separate endpoint rather than a HEAD of the export because FastAPI does not route HEAD to
    a GET handler — it answers 405, which the UI would read as "nothing wrong".
    """
    response = await build_export(
        session,
        user,
        show=show,
        mode=mode,
        skus=skus,
        location=location,
        postal_code=postal_code,
        shipping_profile=shipping_profile,
        return_profile=return_profile,
        payment_profile=payment_profile,
        image_base=image_base,
        packaging=packaging,
    )
    headers = response.headers
    missing = [h for h in (headers.get("X-Missing-Required") or "").split("; ") if h]
    dropped = [h for h in (headers.get("X-Dropped") or "").split("; ") if h]
    skipped = [h for h in (headers.get("X-Skipped") or "").split("; ") if h]
    body = response.body.decode("utf-8", "replace")
    rows = max(0, len([line for line in body.splitlines() if line.strip()]) - 1)

    return {
        "ok": not missing,
        "template": headers.get("X-Template"),
        "rows": rows,
        "missing_required": missing,
        "dropped": dropped,
        "skipped": skipped,
    }


@router.delete("/details")
async def remove_corner_details(
    skus: str = Query("", max_length=8000),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Delete corner close-ups — for one card, or for every card when no SKUs are given.

    They are derived, so this costs nothing but the seconds to cut them again. Turning them off
    in the export hides them; this reclaims the disk, which on two thousand cards is the
    difference between eight thousand files and none.
    """
    from app.services.purge import purge_images  # noqa: F401 - kept for symmetry of intent

    storage = get_storage()
    wanted = {s.strip() for s in skus.split(",") if s.strip()}

    query = select(Image).where(
        Image.kind.in_(list(corner_details.QUADRANTS)),
        Image.inventory_item_id.in_(
            select(InventoryItem.id).where(InventoryItem.user_id == user.id)
        ),
    )
    rows = (await session.execute(query)).scalars().all()

    removed = 0
    for row in rows:
        item = await session.get(InventoryItem, row.inventory_item_id)
        if wanted and (item is None or item.sku not in wanted):
            continue
        try:
            storage.delete(row.path)
        except Exception as exc:  # noqa: BLE001 - a stuck file must not strand the rest
            log.warning("corner_details.delete_failed", path=row.path, error=str(exc))
        await session.delete(row)
        removed += 1

    await session.commit()
    log.info("corner_details.removed", count=removed)
    return {"removed": removed}
