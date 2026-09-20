"""Reference data mirrored from TCGdex, plus the condition rubric.

None of this is user-owned. It is synced, and it is safe to re-sync.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import Condition, Marketplace, PriceType, Severity
from app.models.base import created, pg_enum, pk, updated


class CardSet(Base):
    __tablename__ = "card_sets"

    id: Mapped[uuid.UUID] = pk()
    tcgdex_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    name: Mapped[str] = mapped_column(String(255))
    series_id: Mapped[str | None] = mapped_column(String(64))
    series_name: Mapped[str | None] = mapped_column(String(255))
    logo_url: Mapped[str | None] = mapped_column(Text)
    symbol_url: Mapped[str | None] = mapped_column(Text)
    card_count_official: Mapped[int | None] = mapped_column(Integer)
    card_count_total: Mapped[int | None] = mapped_column(Integer)
    release_date: Mapped[date | None] = mapped_column(Date)
    # Set-level checksum of the upstream payload, so an unchanged set is skipped on re-sync.
    content_hash: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    cards: Mapped[list["Card"]] = relationship(back_populates="card_set")


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[uuid.UUID] = pk()
    tcgdex_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    set_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("card_sets.id", ondelete="CASCADE"), index=True
    )
    local_id: Mapped[str] = mapped_column(String(32), index=True)  # "4" in 4/102
    name: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str | None] = mapped_column(String(64))
    rarity: Mapped[str | None] = mapped_column(String(64))
    illustrator: Mapped[str | None] = mapped_column(String(255))
    hp: Mapped[int | None] = mapped_column(Integer)
    types: Mapped[list | None] = mapped_column(JSONB)
    image_url: Mapped[str | None] = mapped_column(Text)

    # Perceptual hash of the card's official art, as a signed 64-bit integer.
    #
    # Stored on the card rather than in a mirrored image directory: the hashes for the whole
    # English catalogue are under a megabyte, where the art itself would be roughly 2GB. High
    # resolution art is fetched on demand for the few candidates that reach registration.
    #
    # Signed because Postgres has no unsigned 64-bit type; the application converts. Hamming
    # distance is computed on the XOR's popcount, which is sign-agnostic.
    art_phash: Mapped[int | None] = mapped_column(BigInteger, index=True)
    # When hashing last succeeded, so a re-run can skip finished work and retry failures.
    # Spatial grid hash: 16 cell hashes, 1024 bits. The single `art_phash` above averages the
    # whole card into 64 bits and so loses layout, which is most of what distinguishes cards.
    # 128 bytes each, ~3MB for the English catalogue — small enough to hold in memory and
    # compare with numpy rather than pushing the work into SQL.
    art_grid_hash: Mapped[bytes | None] = mapped_column(LargeBinary(128))
    art_hashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    art_hash_error: Mapped[str | None] = mapped_column(Text)
    # Full upstream payload. Cheap insurance against needing a field we did not model.
    raw: Mapped[dict | None] = mapped_column(JSONB)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    card_set: Mapped[CardSet] = relationship(back_populates="cards")
    variants: Mapped[list["CardVariant"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_cards_set_local", "set_id", "local_id"),)


class CardVariant(Base):
    """One row per TCGdex `variants_detailed` entry.

    Inventory points here, never at `cards` — the collectible is the variant (spec §11).
    """

    __tablename__ = "card_variants"

    id: Mapped[uuid.UUID] = pk()
    card_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cards.id", ondelete="CASCADE"), index=True
    )
    # TCGdex's variant identifier. NOT unique: it names a variant *class*, not a row.
    # Every holo rare in Base Set shares the same four variantIds, so treating this as a
    # unique key both fails to insert and, worse, would let a price lookup match the wrong
    # card. Identity is (card_id, type, subtype, size, stamp_key) — see the constraint below.
    tcgdex_variant_id: Mapped[str | None] = mapped_column(String(64), index=True)

    type: Mapped[str | None] = mapped_column(String(32))     # normal, holo, reverse
    subtype: Mapped[str | None] = mapped_column(String(64))  # unlimited, shadowless, ...
    size: Mapped[str | None] = mapped_column(String(32))     # standard, jumbo
    stamp: Mapped[list | None] = mapped_column(JSONB)        # ["1st-edition"]
    # Sorted, joined form of `stamp`, used for uniqueness. Base Set Charizard has two
    # ("holo", "shadowless", "standard") variants that differ only by the 1st-edition
    # stamp, so the shape key must include it or they collapse into one row.
    stamp_key: Mapped[str] = mapped_column(String(128), default="")

    is_normal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_holo: Mapped[bool] = mapped_column(Boolean, default=False)
    is_reverse: Mapped[bool] = mapped_column(Boolean, default=False)
    is_first_edition: Mapped[bool] = mapped_column(Boolean, default=False)
    is_promo: Mapped[bool] = mapped_column(Boolean, default=False)

    # Human-readable label, e.g. "Holo · Shadowless · 1st Edition".
    label: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    card: Mapped[Card] = relationship(back_populates="variants")

    __table_args__ = (
        # A card without upstream variant IDs still needs one row per distinct shape.
        UniqueConstraint(
            "card_id", "type", "subtype", "size", "stamp_key", name="uq_variant_shape"
        ),
    )


class PricingSource(Base):
    __tablename__ = "pricing_sources"

    id: Mapped[uuid.UUID] = pk()
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    license_note: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = created()


class Price(Base):
    """Append-only. Never UPDATE, never DELETE — the history is the point (REQ-DB-003)."""

    __tablename__ = "prices"

    id: Mapped[uuid.UUID] = pk()
    card_variant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("card_variants.id", ondelete="CASCADE"), index=True
    )
    pricing_source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pricing_sources.id", ondelete="RESTRICT"), index=True
    )
    price_type: Mapped[PriceType] = mapped_column(pg_enum(PriceType, "price_type"))
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    # When we recorded it, versus when the upstream said it was last updated.
    captured_at: Mapped[datetime] = created()
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_prices_latest",
            "card_variant_id",
            "pricing_source_id",
            "price_type",
            "captured_at",
        ),
    )


class ConditionRubric(Base):
    """docs/CONDITION.md as queryable rows. Seeded, not user-editable."""

    __tablename__ = "condition_rubric"

    id: Mapped[uuid.UUID] = pk()
    imperfection: Mapped[str] = mapped_column(String(64), index=True)
    condition: Mapped[Condition] = mapped_column(pg_enum(Condition, "condition_enum"))
    severity: Mapped[Severity | None] = mapped_column(pg_enum(Severity, "severity_enum"))
    # Measurement is length in mm, area in mm², or lift in mm — `measure` says which.
    measure: Mapped[str] = mapped_column(String(16))  # length | area | lift | none | any
    max_value: Mapped[float | None] = mapped_column(Numeric(12, 3))
    percent_of_card: Mapped[float | None] = mapped_column(Numeric(8, 4))
    disallowed: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("imperfection", "condition", name="uq_rubric_imperfection_condition"),
    )


class ConditionTranslation(Base):
    """Canonical condition → marketplace code. Seeded but editable in settings (D-004)."""

    __tablename__ = "condition_translations"

    id: Mapped[uuid.UUID] = pk()
    marketplace: Mapped[Marketplace] = mapped_column(pg_enum(Marketplace, "marketplace_enum"))
    condition: Mapped[Condition] = mapped_column(pg_enum(Condition, "condition_enum"))
    external_code: Mapped[str] = mapped_column(String(64))
    external_label: Mapped[str] = mapped_column(String(128))
    requires_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("marketplace", "condition", name="uq_translation_marketplace_condition"),
    )


class EbaySale(Base):
    """One completed eBay sale, kept as evidence rather than folded into an average.

    A median can be recomputed from these and an average cannot be un-averaged — but the reason
    that matters here is that the operator asked to *see* eBay pricing. Three comps with titles
    and prices is something they can judge; a lone figure with no provenance is the thing they
    already distrust.
    """

    __tablename__ = "ebay_sales"

    id: Mapped[uuid.UUID] = pk()
    card_variant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("card_variants.id", ondelete="CASCADE"),
        index=True,
    )
    # The grade the listing stated, if it stated one. Null means unknown, never "any".
    condition: Mapped[str | None] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    item_id: Mapped[str | None] = mapped_column(String(32))
    item_url: Mapped[str | None] = mapped_column(Text)
    # What was searched, so a bad comp can be traced to a bad query.
    query: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
