"""Read-only views over the mirrored TCGdex catalogue."""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.enums import PriceType
from app.models import Card, CardSet, CardVariant, Price, PricingSource
from app.services.pricing import SOURCE_MANUAL, latest_prices

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/stats")
async def stats(session: AsyncSession = Depends(get_session)) -> dict:
    async def count(model) -> int:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()

    return {
        "sets": await count(CardSet),
        "cards": await count(Card),
        "variants": await count(CardVariant),
    }


@router.get("/sets")
async def list_sets(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (
        await session.execute(select(CardSet).order_by(CardSet.release_date.desc().nullslast()))
    ).scalars()
    return [
        {
            "id": str(s.id),
            "tcgdex_id": s.tcgdex_id,
            "name": s.name,
            "series": s.series_name,
            "logo": s.logo_url,
            "cards_official": s.card_count_official,
            "cards_total": s.card_count_total,
            "release_date": s.release_date.isoformat() if s.release_date else None,
        }
        for s in rows
    ]


@router.get("/cards")
async def search_cards(
    q: str | None = Query(None, description="Name fragment"),
    set_id: str | None = Query(None, description="TCGdex set id"),
    number: str | None = Query(None, description="Collector number, e.g. 4"),
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    query = select(Card).options(selectinload(Card.variants), selectinload(Card.card_set))
    joined_sets = False

    # One box, typed the way the card actually reads.
    #
    # A person holding a card does not think in fields, they read what is printed on it:
    # "charmander 49", or "charmander secret wonders". Making them find a set dropdown and a
    # number box to say what is written on the card in front of them is the friction that stops
    # corrections being made at all — and this box exists precisely for the cards recognition
    # got wrong, where a wrong identification is worth real money.
    #
    # So the free-text query is split into a name, an optional trailing collector number, and an
    # optional set name. Anything that does not parse simply stays part of the name, which is
    # the old behaviour.
    if q:
        name, parsed_number, parsed_set = _parse_query(q)
        if parsed_number and not number:
            number = parsed_number
        if name:
            query = query.where(Card.name.ilike(f"%{name}%"))
        if parsed_set and not set_id:
            query = query.join(CardSet, Card.set_id == CardSet.id)
            joined_sets = True
            query = query.where(CardSet.name.ilike(f"%{parsed_set}%"))

    if number:
        # Printed numbers are zero-padded inconsistently across eras: "4", "04" and "004" are
        # the same card, and the operator types whichever they see.
        digits = number.lstrip("0") or number
        query = query.where(
            Card.local_id.in_({number, digits, digits.zfill(2), digits.zfill(3)})
        )
    if set_id:
        if not joined_sets:
            query = query.join(CardSet, Card.set_id == CardSet.id)
            joined_sets = True
        query = query.where(CardSet.tcgdex_id == set_id)

    rows = (await session.execute(query.limit(limit))).scalars().all()
    cards = [_card_dict(c) for c in rows]
    # A number is a near-unique key, so exact matches on it come first; otherwise newer sets
    # first, since a collection being scanned skews modern.
    if number:
        cards.sort(key=lambda c: str(c.get("number") or "").lstrip("0") != number.lstrip("0"))
    return cards


# Set names people actually type, mapped to how the catalogue spells them. Only where the two
# genuinely differ — this is not a place to accumulate every set's nickname.
_SET_ALIASES = {
    "secret wonders": "Secret Wonders",
    "crystal guardians": "Crystal Guardians",
    "legendary treasures": "Legendary Treasures",
    "ancient origins": "Ancient Origins",
    "base set": "Base",
}


def _parse_query(raw: str) -> tuple[str, str | None, str | None]:
    """Split "charmander 49" or "charmander secret wonders" into its parts.

    Returns (name, number, set_name). A trailing number is taken as a collector number, and
    "49/100" is accepted too since that is how it is printed. Remaining words are tested against
    known set names; whatever is left over is the card name.
    """
    text = " ".join(raw.split())
    if not text:
        return "", None, None

    number: str | None = None
    # A trailing "49" or "49/100". Leading tokens are left alone: "Mr. Mime 2" is the number,
    # but "Type 2 Energy" is a name, so only the final token is considered.
    match = re.search(r"(?:^|\s)(\d{1,3})(?:/\d{1,3})?$", text)
    if match:
        number = match.group(1)
        text = text[: match.start()].strip()

    set_name: str | None = None
    lowered = text.lower()
    for alias, canonical in _SET_ALIASES.items():
        if lowered.endswith(alias):
            set_name = canonical
            text = text[: len(text) - len(alias)].strip()
            break

    return text, number, set_name


@router.get("/cards/{tcgdex_id}")
async def get_card(tcgdex_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    card = (
        await session.execute(
            select(Card)
            .options(selectinload(Card.variants), selectinload(Card.card_set))
            .where(Card.tcgdex_id == tcgdex_id)
        )
    ).scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail=f"card not found: {tcgdex_id}")
    return _card_dict(card)


class ManualPriceIn(BaseModel):
    amount: float
    currency: str = "USD"


@router.post("/variants/{variant_id}/price")
async def set_manual_price(
    variant_id: uuid.UUID,
    body: ManualPriceIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Record a price the operator found themselves, usually from eBay completed listings.

    This system cannot see eBay. The Browse API returns *asking* prices, which are what nobody
    paid; sold prices need eBay's Marketplace Insights API, which is access-restricted and has
    to be applied for. TCGplayer market is the best proxy available without that, and for a US
    seller it is a reasonable one — but it is a proxy.

    So the operator can override it. A figure taken from three completed eBay listings for the
    same card in the same condition beats any feed, and it takes precedence over the marketplace
    sources in `value_for_variant` for exactly that reason.
    """
    variant = await session.get(CardVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="no such variant")

    source = (
        await session.execute(
            select(PricingSource).where(PricingSource.code == SOURCE_MANUAL)
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=500, detail="manual pricing source not seeded")

    session.add(
        Price(
            card_variant_id=variant.id,
            pricing_source_id=source.id,
            price_type=PriceType.MARKET,
            currency=body.currency.upper(),
            amount=body.amount,
        )
    )
    await session.commit()
    return {"ok": True, "variant": str(variant.id), "amount": body.amount}


@router.get("/variants/{variant_id}/prices")
async def variant_prices(
    variant_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    prices = await latest_prices(session, variant_id)
    return [
        {
            "type": p.price_type.value,
            "currency": p.currency,
            "amount": float(p.amount),
            "captured_at": p.captured_at.isoformat(),
            "source_updated_at": p.source_updated_at.isoformat() if p.source_updated_at else None,
        }
        for p in prices
    ]


def _card_dict(card: Card) -> dict:
    return {
        "id": str(card.id),
        "tcgdex_id": card.tcgdex_id,
        "name": card.name,
        "number": card.local_id,
        # Same value under the name the rest of the API uses for it.
        "local_id": card.local_id,
        "rarity": card.rarity,
        "image": card.image_url,
        "set": {
            "tcgdex_id": card.card_set.tcgdex_id,
            "name": card.card_set.name,
            "total": card.card_set.card_count_official,
        }
        if card.card_set
        else None,
        "variants": [
            {
                "id": str(v.id),
                "tcgdex_variant_id": v.tcgdex_variant_id,
                "label": v.label,
                "type": v.type,
                "subtype": v.subtype,
                "first_edition": v.is_first_edition,
            }
            for v in card.variants
        ],
    }
