"""Shared column helpers."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


def pg_enum(enum_class: type[enum.Enum], name: str) -> Enum:
    """A Postgres enum that stores member *values*, not member names.

    SQLAlchemy defaults to storing `PriceType.MARKET` as "MARKET" while the API serialises it
    as "market". That split shows up the moment anyone reads the database directly or exports
    a CSV. `values_callable` keeps the stored form identical to the wire form.
    """
    return Enum(enum_class, name=name, values_callable=lambda e: [m.value for m in e])
