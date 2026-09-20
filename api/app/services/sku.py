"""Atomic, gapless SKU allocation per user (REQ-STO-005).

Uses a row lock on the user rather than a sequence, because SKUs must be per-user and gapless;
a Postgres sequence would leak numbers on rollback.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InventoryItem, User
from app.storage.paths import format_sku, parse_sku


async def next_sku(session: AsyncSession, user_id) -> str:
    # Lock the owning user row so two concurrent captures cannot claim the same number.
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())

    highest = (
        await session.execute(
            select(func.max(InventoryItem.sku)).where(InventoryItem.user_id == user_id)
        )
    ).scalar_one_or_none()

    return format_sku(1 if highest is None else parse_sku(highest) + 1)
