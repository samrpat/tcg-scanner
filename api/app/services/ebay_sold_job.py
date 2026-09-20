"""Fetching eBay sold prices for a chosen set of cards.

Kept out of the router because the interesting part is not HTTP, it is deciding what to search
for. A query that is too loose prices the wrong card; too tight and it finds nothing.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logging_setup import get_logger
from app.models import Card, InventoryItem, User
from app.routers.capture import _variant_label
from app.services import ebay_sold
from app.services.listing_draft import ebay_set_name, printed_number

log = get_logger(__name__)


def query_for(card: Card, variant: str | None) -> str:
    """What to search eBay for.

    Name, printed number and set — the same three things that identify the card in eBay's own
    catalogue title (D-111), which is what most sellers write. The number is quoted because
    unquoted it matches any listing containing those digits anywhere.

    Reverse holo is included when it applies: it is a different printing at a different price,
    and letting reverse comps price a normal card overstates it. The other way round — leaving
    "normal" out — is deliberate, since almost nobody writes it.
    """
    number = printed_number(
        card.local_id, card.card_set.card_count_official if card.card_set else None
    )
    parts = [
        card.name,
        f'"{number}"' if number else "",
        ebay_set_name(
            card.card_set.name if card.card_set else None,
            card.card_set.series_id if card.card_set else None,
            card.card_set.series_name if card.card_set else None,
        ),
    ]
    if variant and "Reverse" in variant:
        parts.append("reverse holo")
    return " ".join(p for p in parts if p)


async def fetch_for_skus(
    session: AsyncSession,
    user: User,
    skus: list[str],
    max_results: int | None = None,
) -> dict:
    """Pull sold listings for each selected card and store them.

    One card's failure does not stop the rest: a run over fifty cards that dies on the third
    would cost money and leave nothing behind, so failures are collected and reported.
    """
    items = (
        (
            await session.execute(
                select(InventoryItem)
                .options(
                    selectinload(InventoryItem.card).selectinload(Card.card_set),
                    selectinload(InventoryItem.card_variant),
                )
                .where(InventoryItem.user_id == user.id, InventoryItem.sku.in_(skus))
            )
        )
        .scalars()
        .all()
    )

    done: list[dict] = []
    failed: list[dict] = []
    skipped: list[dict] = []

    for item in items:
        if item.card is None or item.card_variant_id is None:
            skipped.append({"sku": item.sku, "why": "identify the card and its version first"})
            continue

        query = query_for(item.card, _variant_label(item.card_variant))
        try:
            rows = await ebay_sold.fetch(query, max_results)
        except Exception as exc:  # noqa: BLE001 - reported per card, never fatal
            failed.append({"sku": item.sku, "query": query, "error": str(exc)[:200]})
            log.warning("ebay_sold.failed", sku=item.sku, query=query, error=str(exc))
            continue

        sales = ebay_sold.parse_sales(rows, query)
        await ebay_sold.store(session, item.card_variant_id, sales)
        await session.flush()

        price = await ebay_sold.sold_price(
            session,
            item.card_variant_id,
            item.condition.value if item.condition else None,
        )
        done.append(
            {
                "sku": item.sku,
                "query": query,
                "found": len(rows),
                "usable": len(sales),
                "price": price.as_dict() if price else None,
            }
        )
        log.info(
            "ebay_sold.fetched", sku=item.sku, found=len(rows), usable=len(sales)
        )

    await session.commit()
    return {"priced": done, "failed": failed, "skipped": skipped}
