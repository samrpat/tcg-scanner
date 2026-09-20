"""Cross-cutting operational tables: external mappings, jobs, review queue."""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.enums import JobStatus, ReviewCategory, ReviewStatus
from app.models.base import created, pg_enum, pk, updated


class ExternalMapping(Base):
    """Generic link to an outside system (REQ-DB-006).

    One table for Collectr, eBay, TCGplayer and anything later, so adding a provider is a row
    rather than a migration. Deleting mappings never touches the entity they point at.
    """

    __tablename__ = "external_mappings"

    id: Mapped[uuid.UUID] = pk()
    entity_type: Mapped[str] = mapped_column(String(64), index=True)  # inventory_item, card, ...
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)     # collectr, ebay, tcgplayer
    external_id: Mapped[str] = mapped_column(String(255))
    extra: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_id", "provider", "external_id", name="uq_external_mapping"
        ),
        Index("ix_external_provider_lookup", "provider", "external_id"),
    )


class Job(Base):
    """Audit trail for queued work. arq owns the live queue; this is the record."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = pk()
    arq_job_id: Mapped[str | None] = mapped_column(String(128), index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    tag: Mapped[str | None] = mapped_column(String(32))  # ingest | recognize | condition
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"), default=JobStatus.QUEUED
    )
    payload: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)


class Review(Base):
    """Exception queue. High-confidence work bypasses this entirely (spec §17)."""

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[ReviewCategory] = mapped_column(pg_enum(ReviewCategory, "review_category"))
    status: Mapped[ReviewStatus] = mapped_column(
        pg_enum(ReviewStatus, "review_status"), default=ReviewStatus.OPEN
    )
    reason: Mapped[str | None] = mapped_column(Text)
    # Candidate list for the operator, e.g. the 94%/4%/2% choice in spec §12.
    candidates: Mapped[list | None] = mapped_column(JSONB)
    resolution: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created()
    # Set when the operator chose "later". The review stays OPEN — it is still outstanding work
    # — but drops out of the main queue until they ask for it back. Distinct from DISMISSED,
    # which means "this flag was wrong".
    deferred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_reviews_open", "user_id", "status", "category"),
        # At most one open review per item per category. The application also serialises
        # reconciliation, but concurrent workers make this worth enforcing in the schema.
        Index(
            "uq_review_one_open_per_category",
            "inventory_item_id",
            "category",
            unique=True,
            postgresql_where="status = 'open'",
        ),
    )
