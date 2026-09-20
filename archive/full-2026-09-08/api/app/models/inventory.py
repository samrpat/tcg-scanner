"""User-owned inventory. Nothing here may be deleted by an external adapter (spec §23)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
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
from app.enums import (
    AssessmentSource,
    CaptureSource,
    Condition,
    ImageKind,
    InventoryStatus,
    QualityVerdict,
)
from app.models.base import created, pg_enum, pk, updated

if TYPE_CHECKING:  # pragma: no cover - import cycle exists only for the type checker
    from app.models.reference import Card, CardVariant


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class InventoryItem(Base):
    """One physical card. The core entity — everything else references this."""

    __tablename__ = "inventory_items"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    sku: Mapped[str] = mapped_column(String(32), index=True)  # CARD-000001

    # Nullable until recognition runs — an item exists from the moment it is photographed.
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cards.id", ondelete="SET NULL"), index=True
    )
    card_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("card_variants.id", ondelete="SET NULL"), index=True
    )
    identification_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))

    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[InventoryStatus] = mapped_column(
        pg_enum(InventoryStatus, "inventory_status"), default=InventoryStatus.CAPTURED
    )

    # Denormalised accepted condition, for fast filtering. The authoritative record is
    # the condition_assessments row flagged is_accepted.
    condition: Mapped[Condition | None] = mapped_column(pg_enum(Condition, "condition_enum"))
    condition_points: Mapped[int | None] = mapped_column(Integer)

    acquired_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    acquired_currency: Mapped[str | None] = mapped_column(String(3))
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    # Whether the capture actually contains a card. NULL means nobody has said. Set to False
    # for deliberate negative examples — a selfie, an empty frame — so the benchmark scores a
    # non-detection there as a pass instead of a failure, which is what it is.
    # Set when a person has looked at the finished card and confirmed it. Nothing is listable
    # without this, however confident the pipeline was.
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # An eBay item id whose listing this card should be created from, via "Sell one like this".
    # eBay pre-fills category and item specifics from it, and for trading cards those specifics
    # are what makes the listing findable.
    listing_template_item_id: Mapped[str | None] = mapped_column(String(32))
    # The scanning session this card was captured in. Nullable and SET NULL on delete:
    # removing a grouping must never remove photographs of real cards.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("scan_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    # Set when the operator confirms the listing is live on eBay. This is the queue's progress
    # mark, so it is set on their say-so — not when they open the eBay tab, because opening a
    # tab is not listing.
    listed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Overrides entered on the eBay screen. Stored rather than written back to the card so the
    # generated title keeps tracking the catalogue and the price keeps tracking the pricing run;
    # these record that a person disagreed, and with what.
    listing_title: Mapped[str | None] = mapped_column(String(120))
    listing_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # The lot this card is being sold as part of, if any. A card in a lot is not listed alone.
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("lots.id", ondelete="SET NULL"), index=True
    )
    # Set when the operator moved past a card without approving it. Distinct from
    # `rescan_requested_at`: this one means "not now", not "shoot it again".
    approval_deferred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when a capture is too poor to grade from and the card must be photographed again.
    # Cleared automatically when a new original arrives for that card.
    rescan_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    contains_card: Mapped[bool | None] = mapped_column(Boolean)

    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    # Read-only convenience for display: the identified card, when recognition has resolved
    # one. Inventory's real pointer for pricing and listing is card_variant_id (D-012); this is
    # for showing a name next to a thumbnail without a second query.
    card: Mapped["Card | None"] = relationship(lazy="raise_on_sql", viewonly=True)
    # Same treatment as `card`: view-only and never lazily loaded, so a forgotten selectinload
    # fails loudly in a test rather than issuing a query per row in a listing.
    card_variant: Mapped["CardVariant | None"] = relationship(
        lazy="raise_on_sql", viewonly=True
    )

    images: Mapped[list["Image"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    assessments: Mapped[list["ConditionAssessment"]] = relationship(back_populates="item")

    __table_args__ = (
        UniqueConstraint("user_id", "sku", name="uq_inventory_user_sku"),
        Index("ix_inventory_user_status", "user_id", "status"),
    )


class Image(Base):
    __tablename__ = "images"

    id: Mapped[uuid.UUID] = pk()
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ImageKind] = mapped_column(pg_enum(ImageKind, "image_kind"))
    path: Mapped[str] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()

    # Where the capture came from. Lets a fixed-rig capture be told apart from a handheld one.
    source: Mapped[CaptureSource | None] = mapped_column(pg_enum(CaptureSource, "capture_source"))

    # --- Set on processed rows only (docs/IMAGING.md) ---
    # Exact scale of the rectified image. Condition measurement divides by this to get mm.
    px_per_mm: Mapped[float | None] = mapped_column(Numeric(8, 3))
    detection_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    # Which strategy located the card: an edge-strategy name, or "manual" when an operator
    # placed the corners. Worth persisting — a manually corrected card should be visibly
    # distinct from an automatically detected one, in the UI and to Phase 3.
    detection_method: Mapped[str | None] = mapped_column(String(48))
    # The four source-image corners used for the warp, [[x, y], ...] in TL,TR,BR,BL order.
    corners: Mapped[list | None] = mapped_column(JSONB)
    # Degrees of rotation applied to bring the card upright. Phase 3 may revise this once it
    # can read the card and tell upside-down from upright.
    rotation_applied: Mapped[int | None] = mapped_column(Integer)
    quality_verdict: Mapped[QualityVerdict | None] = mapped_column(
        pg_enum(QualityVerdict, "quality_verdict")
    )
    # Raw measurements behind the verdict, so thresholds can be retuned against real captures.
    quality: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    item: Mapped[InventoryItem] = relationship(back_populates="images")

    __table_args__ = (
        UniqueConstraint("inventory_item_id", "kind", name="uq_image_item_kind"),
    )


class ConditionAssessment(Base):
    """Append-only. An AI prediction is never erased by a human correction (spec §16)."""

    __tablename__ = "condition_assessments"

    id: Mapped[uuid.UUID] = pk()
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[AssessmentSource] = mapped_column(
        pg_enum(AssessmentSource, "assessment_source")
    )
    condition: Mapped[Condition] = mapped_column(pg_enum(Condition, "condition_enum"))
    points_total: Mapped[int | None] = mapped_column(Integer)
    # [{"imperfection": "edgewear", "severity": "minor", "points": 2, "measured_mm": 42.0}, ...]
    defects: Mapped[list | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    model_version: Mapped[str | None] = mapped_column(String(64))
    # Exactly one accepted row per item, enforced by a partial unique index.
    is_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created()

    item: Mapped[InventoryItem] = relationship(back_populates="assessments")

    __table_args__ = (
        Index(
            "uq_assessment_one_accepted",
            "inventory_item_id",
            unique=True,
            postgresql_where="is_accepted",
        ),
    )


class Lot(Base):
    """A bundle of cards sold as a single item.

    The fixed costs of an eBay order — the per-order fee and the postage — are paid once per
    *order*, not per card. That is what makes a lot worth more than the sum of its cards when
    each card is individually marginal, and it is the whole reason this table exists.
    """

    __tablename__ = "lots"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    # What the operator decided to ask. The *suggested* figure is computed from the members on
    # demand and deliberately not stored, so it can never disagree with the cards in the lot.
    asking_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class ScanSession(Base):
    """A batch of cards scanned, approved and listed together.

    The open session — the newest with no `closed_at` — is the one new captures join. Starting
    a new session closes the previous one; reopening an old one makes it the target again, which
    is how a batch gets added to after the fact.
    """

    __tablename__ = "scan_sessions"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Crop and render, but never identify. For batches destined for a tool that does its own
    # identification — the rendering is the part nothing else does.
    photos_only: Mapped[bool] = mapped_column(Boolean, default=False)


class EbayUploadTemplate(Base):
    """The header row of an eBay Seller Hub upload template the operator downloaded.

    Stored so the export can emit exactly the columns eBay will accept, rather than the columns
    the documentation implies. `preamble` keeps whatever eBay wrote above the header row — the
    `#INFO` lines are part of the format, and a file that drops them can be rejected outright.
    """

    __tablename__ = "ebay_upload_templates"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    headers: Mapped[list] = mapped_column(JSONB)
    preamble: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
