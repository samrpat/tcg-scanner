"""Idempotent reference-data seeding. Safe to run on every deploy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conditioning import rubric as rubric_data
from app.conditioning import translate as translate_data
from app.logging_setup import get_logger
from app.models import ConditionRubric, ConditionTranslation, PricingSource, User

log = get_logger(__name__)

DEFAULT_USER_EMAIL = "owner@localhost"

PRICING_SOURCES = [
    {
        "code": "tcgdex_tcgplayer",
        "name": "TCGplayer (via TCGdex)",
        "url": "https://tcgdex.dev/",
        "license_note": "MIT-licensed API redistributing TCGplayer market data.",
        "priority": 10,
    },
    {
        "code": "tcgdex_cardmarket",
        "name": "Cardmarket (via TCGdex)",
        "url": "https://tcgdex.dev/",
        "license_note": "MIT-licensed API redistributing Cardmarket data. EUR.",
        "priority": 20,
    },
    {
        "code": "manual",
        "name": "Manual entry",
        "url": None,
        "license_note": "Operator-entered. Always available.",
        "priority": 99,
    },
]


async def seed_pricing_sources(session: AsyncSession) -> int:
    existing = set((await session.execute(select(PricingSource.code))).scalars())
    added = 0
    for row in PRICING_SOURCES:
        if row["code"] in existing:
            continue
        session.add(PricingSource(**row))
        added += 1
    await session.flush()
    return added


async def seed_condition_rubric(session: AsyncSession) -> int:
    existing = {
        (r.imperfection, r.condition)
        for r in (
            await session.execute(select(ConditionRubric))
        ).scalars()
    }
    added = 0
    for row in rubric_data.seed_rows():
        if (row["imperfection"], row["condition"]) in existing:
            continue
        session.add(ConditionRubric(**row))
        added += 1
    await session.flush()
    return added


async def seed_condition_translations(session: AsyncSession, *, reset: bool = False) -> int:
    """Insert missing translations.

    Translations are user-editable in settings, so an ordinary seed never overwrites what is
    already there. `reset` re-applies the shipped defaults, which is what you want after a
    correction to the defaults themselves — at the cost of discarding local edits.
    """
    current = {
        (t.marketplace, t.condition): t
        for t in (await session.execute(select(ConditionTranslation))).scalars()
    }
    changed = 0
    for row in translate_data.seed_rows():
        key = (row["marketplace"], row["condition"])
        existing = current.get(key)
        if existing is None:
            session.add(ConditionTranslation(**row))
            changed += 1
        elif reset:
            for field, value in row.items():
                setattr(existing, field, value)
            changed += 1
    await session.flush()
    return changed


async def seed_default_user(session: AsyncSession) -> int:
    found = (
        await session.execute(select(User).where(User.email == DEFAULT_USER_EMAIL))
    ).scalar_one_or_none()
    if found:
        return 0
    session.add(User(email=DEFAULT_USER_EMAIL, display_name="Owner"))
    await session.flush()
    return 1


async def seed_all(session: AsyncSession, *, reset: bool = False) -> dict:
    result = {
        "users": await seed_default_user(session),
        "pricing_sources": await seed_pricing_sources(session),
        "condition_rubric": await seed_condition_rubric(session),
        "condition_translations": await seed_condition_translations(session, reset=reset),
    }
    await session.commit()
    log.info("seed.done", **result)
    return result
