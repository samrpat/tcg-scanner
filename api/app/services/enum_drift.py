"""Detect disagreement between the running code's enums and the database's.

Adding a value to a Python `StrEnum` is free; adding it to a Postgres enum needs a migration.
Get the order wrong — write rows using a new value while a long-running container still holds
the old enum — and SQLAlchemy raises `LookupError` when it *reads* those rows, taking out every
endpoint that touches the table rather than only the new rows. A single stale container turned
`/capture/recent` into a 500 that way.

The mismatch is between a running process and a live schema, so it cannot be caught by a test.
It belongs in the health check, where a stale deploy shows up immediately instead of at the
first read.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import enums

# Python enum -> Postgres type name. Only those whose values are persisted.
TRACKED: dict[str, str] = {
    "image_kind": "ImageKind",
    "inventory_status": "InventoryStatus",
    "capture_source": "CaptureSource",
    "quality_verdict": "QualityVerdict",
    "condition_enum": "Condition",
    "severity_enum": "Severity",
    "marketplace_enum": "Marketplace",
    "price_type": "PriceType",
    "job_status": "JobStatus",
    "review_category": "ReviewCategory",
    "review_status": "ReviewStatus",
}


def compare(code: dict[str, set[str]], database: dict[str, set[str]]) -> list[str]:
    """Values the code can produce that the database will not accept."""
    problems: list[str] = []
    for type_name, expected in code.items():
        known = database.get(type_name)
        if known is None:
            problems.append(f"{type_name}: type missing from the database")
            continue
        missing = sorted(expected - known)
        if missing:
            problems.append(
                f"{type_name}: database does not accept {', '.join(missing)} — "
                "a migration has not been applied, or this process is running old code"
            )
    return problems


async def check(session: AsyncSession) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT t.typname, e.enumlabel FROM pg_type t "
                "JOIN pg_enum e ON e.enumtypid = t.oid"
            )
        )
    ).all()

    database: dict[str, set[str]] = {}
    for type_name, label in rows:
        database.setdefault(type_name, set()).add(label)

    code: dict[str, set[str]] = {}
    for type_name, enum_name in TRACKED.items():
        enum_class = getattr(enums, enum_name, None)
        if enum_class is not None:
            code[type_name] = {member.value for member in enum_class}

    return compare(code, database)
