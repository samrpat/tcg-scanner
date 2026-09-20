"""What cards actually sold for on eBay, in the condition being sold.

The feed price (TCGplayer via TCGdex) is a different marketplace with different economics. It
runs low against eBay on cheap cards — a card the feed calls $0.18 routinely sells for $0.99
there — and the gap is not a constant, so no multiplier fixes it. The only honest answer to
"what is this worth on eBay" is what one like it sold for on eBay.

Sales are fetched through Apify's eBay scraper in `sold` mode, which returns completed listings
without an eBay developer token. Two things about that are deliberate:

- **It costs money per result**, so nothing here runs automatically. Sales are fetched for cards
  the operator selects, and stored, and reused until they go stale.
- **It is scraping, not an eBay API.** eBay's own Marketplace Insights API is the sanctioned
  route to sold data but is gated behind an application. That trade-off is the operator's to
  make; this module is the plumbing either way, and `parse_sales` is deliberately independent of
  where the rows came from so another source can be dropped in.

The hard part is not fetching, it is **matching the condition**. A Near Mint comp does not price
a Heavily Played card, and using one for the other is how a listing gets a bad review or a card
gets given away. eBay states a condition on trading card listings, and sellers additionally put
grades in titles; both are read, the explicit field first.
"""

from __future__ import annotations

import re
import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging_setup import get_logger
from app.models import EbaySale

log = get_logger(__name__)

APIFY_ACTOR = "logiover~ebay-scraper"
APIFY_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"

# eBay's own condition labels for ungraded trading cards, which is the strongest signal there is.
_EBAY_CONDITION = {
    "near mint or better": "NM",
    "excellent": "LP",
    "very good": "MP",
    "good": "HP",
    "poor": "DMG",
}

# Grades sellers write into titles, longest first so "near mint" wins over "mint".
_TITLE_GRADES: list[tuple[str, str]] = [
    (r"\bnear\s*mint\b", "NM"),
    (r"\bnm[/\s-]*m\b", "NM"),
    (r"\bmint\b", "NM"),
    (r"\blightly\s*played\b", "LP"),
    (r"\bmoderately\s*played\b", "MP"),
    (r"\bheavily\s*played\b", "HP"),
    (r"\bdamaged\b", "DMG"),
    (r"\bexcellent\b", "LP"),
    (r"\bvery\s*good\b", "MP"),
    (r"\bnm\b", "NM"),
    (r"\blp\b", "LP"),
    (r"\bmp\b", "MP"),
    (r"\bhp\b", "HP"),
    (r"\bdmg\b", "DMG"),
]

# A graded slab is a different product at a different price and must never price a raw card.
_GRADED = re.compile(
    r"\b(psa|bgs|cgc|sgc|ace)\s*\d|\bgraded\b|\bslab\b|\bgem\s*mt\b", re.I
)

# Listings that are not one card.
_NOT_A_SINGLE = re.compile(
    r"\b(lot|bundle|playset|collection|binder|bulk|sealed|pack|box|choose|pick\s*your)\b", re.I
)


@dataclass(frozen=True)
class SoldPrice:
    """A price backed by actual sales, with the evidence attached."""

    median: float
    low: float
    high: float
    count: int
    condition: str | None
    exact_condition: bool  # False when widened because the exact grade had too few comps

    def as_dict(self) -> dict:
        return {
            "median": round(self.median, 2),
            "low": round(self.low, 2),
            "high": round(self.high, 2),
            "count": self.count,
            "condition": self.condition,
            "exact_condition": self.exact_condition,
        }


def condition_of(title: str, ebay_condition: str | None) -> str | None:
    """The grade this listing is for, or None when it does not say.

    eBay's own condition field wins over the title: a seller who set it chose from a fixed list,
    while a title is free text where "mint condition card, NM/M" and "not mint" both appear.
    """
    text = (ebay_condition or "").strip().lower()
    for phrase, grade in _EBAY_CONDITION.items():
        if phrase in text:
            return grade

    lowered = (title or "").lower()
    for pattern, grade in _TITLE_GRADES:
        if re.search(pattern, lowered):
            return grade
    return None


def is_comparable(title: str) -> bool:
    """Whether this listing prices one raw single of the card.

    Graded slabs and multi-card lots are the two things that wreck a median: a PSA 10 sells for
    twenty times the raw card, and a 100-card lot for a fraction of one good one.
    """
    if _GRADED.search(title or ""):
        return False
    return not _NOT_A_SINGLE.search(title or "")


def parse_sales(rows: list[dict], query: str) -> list[dict]:
    """Turn scraper output into rows worth storing, dropping what cannot price this card."""
    parsed: list[dict] = []
    for row in rows:
        title = (row.get("title") or "").strip()
        price = row.get("price")
        if not title or price in (None, "", 0):
            continue
        try:
            amount = float(price)
        except (TypeError, ValueError):
            continue
        if amount <= 0 or not is_comparable(title):
            continue

        item_url = row.get("url") or row.get("itemUrl") or row.get("link")
        item_id = None
        if item_url:
            match = re.search(r"/itm/(?:.*?/)?(\d{9,15})", str(item_url))
            item_id = match.group(1) if match else None

        parsed.append(
            {
                "title": title[:500],
                "price": round(amount, 2),
                "shipping": _number(row.get("shippingCost")),
                "currency": (row.get("currency") or "USD")[:3],
                "condition": condition_of(title, row.get("condition")),
                "sold_at": _date(row.get("soldDate") or row.get("dateSold")),
                "item_id": item_id,
                "item_url": str(item_url)[:1000] if item_url else None,
                "query": query,
            }
        )
    return parsed


def _number(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _date(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


async def fetch(query: str, max_results: int | None = None) -> list[dict]:
    """Ask Apify for completed sold listings matching `query`.

    Raises rather than returning empty on a configuration or transport problem, because "no
    sales found" and "the token is wrong" must not look the same to the caller — the first is a
    fact about the card and the second is a fact about the setup.
    """
    if not settings.apify_token:
        raise RuntimeError(
            "No Apify token set. Put APIFY_TOKEN in .env to fetch eBay sold prices."
        )

    limit = max_results or settings.ebay_sold_max_results
    payload = {
        "mode": "sold",
        "query": query,
        "maxResults": limit,
        "domain": "ebay.com",
        # Item specifics would be useful but cost an extra page fetch per item, and the
        # condition is already on the search result.
        "enrichDetails": False,
    }
    async with httpx.AsyncClient(timeout=settings.ebay_sold_timeout) as client:
        response = await client.post(
            APIFY_URL,
            params={"token": settings.apify_token},
            json=payload,
        )
        response.raise_for_status()
        rows = response.json()
    return rows if isinstance(rows, list) else []


async def store(
    session: AsyncSession, card_variant_id: uuid.UUID, sales: list[dict]
) -> int:
    """Save sales, updating any listing already seen rather than duplicating it."""
    now = datetime.now(UTC)
    written = 0
    for sale in sales:
        values = {
            "id": uuid.uuid4(),
            "card_variant_id": card_variant_id,
            "fetched_at": now,
            **sale,
        }
        if sale.get("item_id"):
            statement = (
                insert(EbaySale)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["card_variant_id", "item_id"],
                    index_where=EbaySale.item_id.isnot(None),
                    set_={
                        "price": values["price"],
                        "title": values["title"],
                        "condition": values["condition"],
                        "fetched_at": now,
                    },
                )
            )
        else:
            statement = insert(EbaySale).values(**values)
        await session.execute(statement)
        written += 1
    return written


async def sold_price(
    session: AsyncSession,
    card_variant_id: uuid.UUID,
    condition: str | None,
    max_age_days: int | None = None,
) -> SoldPrice | None:
    """The median of comparable sales, in this condition where there are enough of them.

    The median rather than the mean: one relisted-at-$40 outlier should not move the number,
    and with a handful of comps it otherwise would.

    When the exact grade has fewer than `ebay_sold_min_comps` sales the search widens to every
    condition, and `exact_condition` records that it did — a number derived from mixed grades is
    still worth having, but the caller must be able to say so rather than present it as a
    like-for-like price.
    """
    age = max_age_days if max_age_days is not None else settings.ebay_sold_max_age_days
    cutoff = datetime.now(UTC) - timedelta(days=age)

    rows = (
        (
            await session.execute(
                select(EbaySale).where(
                    EbaySale.card_variant_id == card_variant_id,
                    EbaySale.fetched_at >= cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    exact = [r for r in rows if condition and r.condition == condition]
    if len(exact) >= settings.ebay_sold_min_comps:
        chosen, exact_condition = exact, True
    else:
        chosen, exact_condition = rows, False

    prices = sorted(float(r.price) for r in chosen)
    if not prices:
        return None

    return SoldPrice(
        median=statistics.median(prices),
        low=prices[0],
        high=prices[-1],
        count=len(prices),
        condition=condition if exact_condition else None,
        exact_condition=exact_condition,
    )
