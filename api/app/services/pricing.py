"""Pricing. Providers are pluggable; Collectr is deliberately absent (D-001).

Every observation is appended with its source, currency, type and two timestamps: when we
recorded it and when the upstream said it last changed. Nothing is ever overwritten.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import PriceType
from app.logging_setup import get_logger
from app.models import Card, CardVariant, Price, PricingSource
from app.services.tcgdex import TCGdexClient, normalize_variants

log = get_logger(__name__)

# Seconds between price requests. A full owned-inventory run of 2,000 cards takes about three
# minutes at this rate, which is nothing against how long the prices stay useful.
PRICE_REQUEST_DELAY_S = 0.08

SOURCE_TCGPLAYER = "tcgdex_tcgplayer"
SOURCE_CARDMARKET = "tcgdex_cardmarket"
SOURCE_MANUAL = "manual"


@dataclass(frozen=True)
class Observation:
    source_code: str
    price_type: PriceType
    currency: str
    amount: float
    source_updated_at: datetime | None = None


class PricingProvider(Protocol):
    code: str

    async def observe(self, payload: dict) -> list[Observation]:
        """Extract price observations for one variant from a provider payload."""
        ...


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# TCGplayer nests prices under a printing key (normal, holofoil, reverseHolofoil, ...).
_TCGPLAYER_FIELDS: dict[str, PriceType] = {
    "marketPrice": PriceType.MARKET,
    "lowPrice": PriceType.LOW,
    "midPrice": PriceType.MID,
    "highPrice": PriceType.HIGH,
    "directLowPrice": PriceType.DIRECT_LOW,
}

_CARDMARKET_FIELDS: dict[str, PriceType] = {
    "avg": PriceType.MARKET,
    "low": PriceType.LOW,
    "trend": PriceType.TREND,
    "avg1": PriceType.AVG1,
    "avg7": PriceType.AVG7,
    "avg30": PriceType.AVG30,
}


# TCGplayer splits its block by printing. The keys are theirs, not ours.
_TCGPLAYER_PRINTING = {
    "normal": ("normal",),
    "holo": ("holofoil", "1st-edition-holofoil"),
    "reverse": ("reverse-holofoil", "reverseHolofoil"),
}

# Cardmarket puts both printings in ONE block, the foil figures under "-holo" suffixes.
_CARDMARKET_SUFFIX = {"normal": "", "holo": "-holo", "reverse": "-holo"}


class TCGdexPricingProvider:
    """Reads the `pricing` block TCGdex attaches to each detailed variant.

    The block describes *every* printing of the card, not the one variant being priced, and both
    marketplaces express that differently: TCGplayer nests a sub-object per printing
    ("normal", "reverse-holofoil"), while Cardmarket keeps one flat block and suffixes the foil
    figures with "-holo".

    Reading it without regard to which printing is being priced is a real mispricing, not a
    rounding error. Measured on me04-037 Meowstic: TCGplayer market is $0.24 reverse-holofoil
    and $0.08 normal — a factor of three — and every one of those figures was previously written
    against both variants. Cardmarket was worse: only the unsuffixed keys were read, so a
    reverse holo was priced as a normal and the "-holo" figures were discarded entirely.

    `finish` is the variant's printing, so each variant gets only its own prices.
    """

    code = "tcgdex"

    async def observe(self, pricing: dict, finish: str = "normal") -> list[Observation]:
        observations: list[Observation] = []

        tcgplayer = pricing.get("tcgplayer") or {}
        updated = _ts(tcgplayer.get("updated"))
        currency = tcgplayer.get("unit") or "USD"
        wanted = _TCGPLAYER_PRINTING.get(finish, ("normal",))
        block = next(
            (tcgplayer[k] for k in wanted if isinstance(tcgplayer.get(k), dict)),
            None,
        )
        if block is not None:
            for field, price_type in _TCGPLAYER_FIELDS.items():
                amount = block.get(field)
                if isinstance(amount, int | float) and amount > 0:
                    observations.append(
                        Observation(SOURCE_TCGPLAYER, price_type, currency, float(amount), updated)
                    )

        cardmarket = pricing.get("cardmarket") or {}
        if cardmarket:
            cm_updated = _ts(cardmarket.get("updated"))
            cm_currency = cardmarket.get("unit") or "EUR"
            suffix = _CARDMARKET_SUFFIX.get(finish, "")
            for field, price_type in _CARDMARKET_FIELDS.items():
                # Fall back to the unsuffixed figure when a card has no separate foil price,
                # which is the case for cards printed in one finish only.
                amount = cardmarket.get(f"{field}{suffix}")
                if amount is None and suffix:
                    amount = cardmarket.get(field)
                if isinstance(amount, int | float) and amount > 0:
                    observations.append(
                        Observation(
                            SOURCE_CARDMARKET, price_type, cm_currency, float(amount), cm_updated
                        )
                    )

        return observations


class ManualPricingProvider:
    """Operator-entered price. Always available, always the last resort."""

    code = SOURCE_MANUAL

    async def observe(self, payload: dict) -> list[Observation]:
        amount = payload.get("amount")
        if amount is None:
            return []
        return [
            Observation(
                SOURCE_MANUAL,
                PriceType(payload.get("price_type", PriceType.MARKET)),
                payload.get("currency", "USD"),
                float(amount),
            )
        ]


async def _source_ids(session: AsyncSession) -> dict[str, object]:
    rows = (await session.execute(select(PricingSource))).scalars().all()
    return {row.code: row.id for row in rows}


async def ingest_prices(
    session: AsyncSession,
    *,
    set_ids: list[str] | None = None,
    limit: int | None = None,
    owned_only: bool = False,
    stale_after_days: int | None = None,
) -> dict:
    """Fetch current prices for stored variants and append observations.

    Deliberately re-reads cards from TCGdex rather than trusting `cards.raw`, because prices
    move and the stored payload is a snapshot from sync time.

    `owned_only` restricts the run to cards actually in inventory, which is almost always what
    is wanted. This is an inventory system, not a price database: pricing the full catalogue
    means 23,544 requests to value cards nobody owns, and it was that request volume that got
    this machine blocked by TCGdex once already (D-069). Two thousand owned cards is a different
    proposition entirely.

    `stale_after_days` skips cards already priced recently, so a re-run costs only what has aged
    out and can be repeated safely.
    """
    provider = TCGdexPricingProvider()
    sources = await _source_ids(session)
    missing = {SOURCE_TCGPLAYER, SOURCE_CARDMARKET} - set(sources)
    if missing:
        raise RuntimeError(f"pricing sources not seeded: {sorted(missing)}. Run `make seed`.")

    # Ordered so that --limit selects a reproducible slice rather than an arbitrary one.
    query = (
        select(Card.tcgdex_id)
        .join(CardVariant, CardVariant.card_id == Card.id)
        .distinct()
        .order_by(Card.tcgdex_id)
    )
    if set_ids:
        from app.models import CardSet

        query = query.join(CardSet, Card.set_id == CardSet.id).where(CardSet.tcgdex_id.in_(set_ids))
    if owned_only:
        from app.models import InventoryItem

        owned = select(InventoryItem.card_id).where(InventoryItem.card_id.is_not(None))
        query = query.where(Card.id.in_(owned))
    if stale_after_days is not None:
        # A card whose newest observation is younger than the window needs nothing doing.
        cutoff = datetime.now(UTC) - timedelta(days=stale_after_days)
        fresh = (
            select(CardVariant.card_id)
            .join(Price, Price.card_variant_id == CardVariant.id)
            .where(Price.captured_at >= cutoff)
        )
        query = query.where(Card.id.not_in(fresh))
    if limit:
        query = query.limit(limit)

    card_ids = (await session.execute(query)).scalars().all()
    written = 0
    skipped = 0
    errors: list[str] = []

    async with TCGdexClient() as client:
        for index, card_id in enumerate(card_ids):
            # Paced deliberately. The set-prior fallback once issued enough requests to get this
            # machine blocked outright (D-069), and a price run is never urgent.
            if index:
                await asyncio.sleep(PRICE_REQUEST_DELAY_S)
            try:
                payload = await client.get_card(card_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{card_id}: {exc}")
                continue

            for row in normalize_variants(payload):
                pricing = row.get("pricing") or {}
                if not pricing:
                    skipped += 1
                    continue

                variant = await _find_variant(session, card_id, row)
                if variant is None:
                    skipped += 1
                    continue

                finish = (
                    "reverse"
                    if variant.is_reverse
                    else "holo"
                    if variant.is_holo
                    else "normal"
                )
                for obs in await provider.observe(pricing, finish):
                    session.add(
                        Price(
                            card_variant_id=variant.id,
                            pricing_source_id=sources[obs.source_code],
                            price_type=obs.price_type,
                            currency=obs.currency,
                            amount=obs.amount,
                            source_updated_at=obs.source_updated_at,
                        )
                    )
                    written += 1
            await session.commit()

    result = {"variants_priced": written, "skipped": skipped, "errors": errors[:20],
              "error_count": len(errors), "cards_checked": len(card_ids)}
    log.info("pricing.done", **result)
    return result


async def _find_variant(
    session: AsyncSession, card_tcgdex_id: str, row: dict
) -> CardVariant | None:
    # Deliberately no global tcgdex_variant_id lookup: that id is shared by every card of the
    # same variant class, so matching on it alone would price Charizard off Machop's data.
    return (
        await session.execute(
            select(CardVariant)
            .join(Card, Card.id == CardVariant.card_id)
            .where(
                Card.tcgdex_id == card_tcgdex_id,
                CardVariant.type == row["type"],
                CardVariant.subtype == row["subtype"],
                CardVariant.size == row["size"],
                CardVariant.stamp_key == row["stamp_key"],
            )
        )
    ).scalar_one_or_none()


async def latest_prices(session: AsyncSession, card_variant_id) -> list[Price]:
    """Newest observation per (source, type) for one variant."""
    from sqlalchemy import func

    newest = (
        select(
            Price.pricing_source_id,
            Price.price_type,
            func.max(Price.captured_at).label("captured_at"),
        )
        .where(Price.card_variant_id == card_variant_id)
        .group_by(Price.pricing_source_id, Price.price_type)
        .subquery()
    )
    rows = await session.execute(
        select(Price).join(
            newest,
            (Price.pricing_source_id == newest.c.pricing_source_id)
            & (Price.price_type == newest.c.price_type)
            & (Price.captured_at == newest.c.captured_at),
        ).where(Price.card_variant_id == card_variant_id)
    )
    return list(rows.scalars())


@dataclass(frozen=True)
class CardValue:
    """What one card is worth, and how much that figure should be trusted."""

    amount: float | None            # market price for a Near Mint copy
    currency: str
    source: str | None
    price_type: str | None
    captured_at: datetime | None
    age_days: int | None
    stale: bool
    condition: str | None = None    # the condition the adjustment was made for
    multiplier: float | None = None
    adjusted: float | None = None   # the reference price scaled for condition
    ebay_estimate: float | None = None  # what it would realistically fetch on eBay
    floored: bool = False           # True when the eBay floor, not the feed, set the estimate
    net: float | None = None        # what lands after eBay's cut and postage
    verdict: str | None = None      # "list" | "marginal" | "bulk"
    sold: dict | None = None        # Median of real eBay sales, when any have been fetched
    break_even: float | None = None # the asking price at which a solo listing clears zero

    def as_dict(self) -> dict:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "source": self.source,
            "price_type": self.price_type,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "age_days": self.age_days,
            "stale": self.stale,
            "condition": self.condition,
            "multiplier": self.multiplier,
            "adjusted": self.adjusted,
            "ebay_estimate": self.ebay_estimate,
            "floored": self.floored,
            "net": self.net,
            "verdict": self.verdict,
            "sold": self.sold,
            "break_even": self.break_even,
        }


def _net_proceeds(asking: float) -> float:
    """What the seller keeps on a single-card order.

    Who pays the postage changes this completely, which is why it is a setting rather than an
    assumption:

    - **Buyer pays** (the default): the postage is reimbursed, but eBay charges its percentage on
      the shipping as well as the item, so the seller still loses a fee on money that only passed
      through.
    - **Free shipping**: the seller absorbs the whole postage cost on top of the fees, which at
      $1.30 puts the break-even above a dollar and turns most of a bulk collection into a loss.

    The fixed per-order fee is the term that actually decides things down here. At $0.30 it is a
    larger bite than the percentage on anything under about $2.
    """
    from app.config import settings

    fee_base = (
        asking + settings.ebay_shipping_cost
        if settings.ebay_buyer_pays_shipping
        else asking
    )
    proceeds = asking - fee_base * settings.ebay_fee_fraction - settings.ebay_fixed_fee
    if not settings.ebay_buyer_pays_shipping:
        proceeds -= settings.ebay_shipping_cost
    return round(proceeds, 2)


def _break_even() -> float:
    """The asking price at which a single-card order first clears zero."""
    from app.config import settings

    fee = settings.ebay_fee_fraction
    fixed = settings.ebay_fixed_fee
    ship = settings.ebay_shipping_cost
    if settings.ebay_buyer_pays_shipping:
        # asking*(1-fee) = fee*ship + fixed
        return round((fee * ship + fixed) / (1 - fee), 2)
    return round((fixed + ship) / (1 - fee), 2)


async def value_for_variant(
    session: AsyncSession, card_variant_id, condition: str | None = None
) -> CardValue:
    """The market price for a variant, with its age.

    "Market" specifically, not low or high. Low is what the most desperate seller accepted and
    high is frequently a typo — the earlier ingest shows a TCGplayer high averaging $2,673
    against a market average of $44. Market is the only one of the five that describes what a
    card actually changes hands for.

    Age travels with the figure rather than being checked separately, because a price without
    its date is a number with no way to tell whether it is information or a fossil.
    """
    from app.config import settings

    blank = CardValue(None, settings.price_display_currency, None, None, None, None, True)
    if card_variant_id is None:
        return blank

    rows = await latest_prices(session, card_variant_id)
    market = [
        p
        for p in rows
        if p.price_type is PriceType.MARKET
        and p.currency == settings.price_display_currency
    ]
    if not market:
        # No figure in the quoting currency. Say nothing rather than quote a euro price to
        # someone listing in dollars.
        return blank

    # A manually entered price outranks a marketplace feed: it is the operator saying what this
    # card actually sells for, usually from completed eBay listings they looked at themselves,
    # which is better evidence than any proxy this system can fetch.
    source_codes = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(PricingSource.id, PricingSource.code).where(
                    PricingSource.id.in_({p.pricing_source_id for p in market})
                )
            )
        ).all()
    }
    market.sort(
        key=lambda p: (
            0 if source_codes.get(p.pricing_source_id) == SOURCE_MANUAL else 1,
            -p.captured_at.timestamp(),
        )
    )
    price = market[0]

    age = (datetime.now(UTC) - price.captured_at).days
    source_name = None
    source = await session.get(PricingSource, price.pricing_source_id)
    if source is not None:
        source_name = source.name

    amount = float(price.amount)
    multiplier = settings.price_condition_multipliers.get(condition or "NM")
    adjusted = round(amount * multiplier, 2) if multiplier is not None else None

    # Real eBay sales beat every estimate here. The feed is a different marketplace and the
    # condition multipliers are a model of one; a median of actual completed listings in this
    # grade is the thing both were standing in for. Fetched on demand and cached, so this is
    # simply absent for most cards.
    from app.services.ebay_sold import sold_price as _sold_price

    sold = await _sold_price(session, card_variant_id, condition)

    # The verdict must be judged against what the card fetches *on eBay*, not against a
    # TCGplayer quote. A manually entered price is already an eBay figure and is left alone;
    # a feed price below eBay's practical floor is raised to it.
    if sold is not None and sold.exact_condition:
        # Comps in this exact grade: no multiplier, no floor, no adjustment. This *is* the
        # eBay price, and dressing it up with a model would only make it less true.
        ebay_estimate, floored = round(sold.median, 2), False
    elif adjusted is None:
        ebay_estimate = None
        floored = False
    elif source_codes.get(price.pricing_source_id) == SOURCE_MANUAL:
        ebay_estimate, floored = adjusted, False
    else:
        floored = adjusted < settings.ebay_floor_price
        ebay_estimate = max(adjusted, settings.ebay_floor_price)

    net = _net_proceeds(ebay_estimate) if ebay_estimate is not None else None
    break_even = _break_even()

    # A card the feed prices under `bulk_below` in Near Mint is bulk, full stop, and is not
    # worth an eBay lookup or a listing of its own. Judged on the *unadjusted* feed price so
    # the line means one thing — "TCGplayer NM under fifty cents" — rather than moving with
    # the card's condition, which would put a played copy of a dollar card in with the chaff.
    if amount < settings.bulk_below and sold is None:
        verdict = "bulk"
    elif net is None:
        verdict = None
    elif net >= settings.ebay_min_net:
        verdict = "list"
    elif net > 0:
        verdict = "marginal"
    else:
        verdict = "bulk"

    return CardValue(
        amount=amount,
        currency=price.currency,
        source=source_name,
        price_type=price.price_type.value,
        captured_at=price.captured_at,
        age_days=age,
        stale=age > settings.price_stale_after_days,
        condition=condition,
        multiplier=multiplier,
        adjusted=adjusted,
        ebay_estimate=ebay_estimate,
        floored=floored,
        net=net,
        verdict=verdict,
        break_even=break_even,
        sold=sold.as_dict() if sold else None,
    )

