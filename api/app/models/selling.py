"""Listings and marketplace links. Removing any of this leaves inventory intact."""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import ListingKind, ListingStatus, Marketplace
from app.models.base import created, pg_enum, pk, updated


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ListingKind] = mapped_column(pg_enum(ListingKind, "listing_kind"))
    status: Mapped[ListingStatus] = mapped_column(
        pg_enum(ListingStatus, "listing_status"), default=ListingStatus.DRAFT
    )
    title: Mapped[str | None] = mapped_column(String(80))  # eBay caps titles at 80 characters
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    items: Mapped[list["ListingItem"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )


class ListingItem(Base):
    """Join table. A lot is one listing with many rows here."""

    __tablename__ = "listing_items"

    id: Mapped[uuid.UUID] = pk()
    listing_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    # RESTRICT, not CASCADE: an inventory item cannot vanish out from under a live listing.
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    listing: Mapped[Listing] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("listing_id", "inventory_item_id", name="uq_listing_item"),
    )


class MarketplaceAccount(Base):
    """Credentials are NOT stored here. `credentials_ref` names an environment key (spec §42)."""

    __tablename__ = "marketplace_accounts"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    marketplace: Mapped[Marketplace] = mapped_column(pg_enum(Marketplace, "marketplace_enum"))
    label: Mapped[str | None] = mapped_column(String(255))
    credentials_ref: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="disconnected")
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    __table_args__ = (
        UniqueConstraint("user_id", "marketplace", "label", name="uq_marketplace_account"),
    )


class MarketplaceListing(Base):
    __tablename__ = "marketplace_listings"

    id: Mapped[uuid.UUID] = pk()
    listing_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    marketplace_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("marketplace_accounts.id", ondelete="SET NULL")
    )
    marketplace: Mapped[Marketplace] = mapped_column(pg_enum(Marketplace, "marketplace_enum"))
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ListingStatus] = mapped_column(
        pg_enum(ListingStatus, "listing_status"), default=ListingStatus.DRAFT
    )
    payload: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()
