"""Mirror TCGdex into Postgres.

Incremental by default: cards already present are not refetched. `refresh=True` refetches
everything and updates rows whose content hash changed. Safe to interrupt at any point —
each card is committed on its own, so a re-run resumes rather than restarting (REQ-SYNC-004).
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging_setup import get_logger
from app.models import Card, CardSet, CardVariant
from app.services.tcgdex import TCGdexClient, normalize_card, normalize_set, normalize_variants

log = get_logger(__name__)


@dataclass
class SyncReport:
    sets_seen: int = 0
    sets_written: int = 0
    cards_seen: int = 0
    cards_written: int = 0
    cards_skipped: int = 0
    variants_written: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sets_seen": self.sets_seen,
            "sets_written": self.sets_written,
            "cards_seen": self.cards_seen,
            "cards_written": self.cards_written,
            "cards_skipped": self.cards_skipped,
            "variants_written": self.variants_written,
            "errors": self.errors[:20],
            "error_count": len(self.errors),
        }


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


async def _upsert_set(
    session: AsyncSession, payload: dict, *, force: bool = False
) -> tuple[CardSet, bool]:
    data = normalize_set(payload)
    existing = (
        await session.execute(select(CardSet).where(CardSet.tcgdex_id == data["tcgdex_id"]))
    ).scalar_one_or_none()

    if existing and existing.content_hash == data["content_hash"] and not force:
        return existing, False

    target = existing or CardSet(tcgdex_id=data["tcgdex_id"], language=settings.tcgdex_language)
    target.name = data["name"]
    target.series_id = data["series_id"]
    target.series_name = data["series_name"]
    target.logo_url = data["logo_url"]
    target.symbol_url = data["symbol_url"]
    target.card_count_official = data["card_count_official"]
    target.card_count_total = data["card_count_total"]
    target.release_date = _parse_date(data["release_date"])
    target.content_hash = data["content_hash"]
    target.synced_at = datetime.now().astimezone()

    if existing is None:
        session.add(target)
    await session.flush()
    return target, True


async def _upsert_card(
    session: AsyncSession, card_set: CardSet, payload: dict, *, force: bool = False
) -> tuple[bool, int]:
    """Returns (written, variants_written).

    `force` rewrites the row even when the upstream payload is byte-identical. Without it a
    change to our own normalisation — a variant label, a new derived column — could never
    reach rows already stored, because the content hash would still match.
    """
    data = normalize_card(payload)
    existing = (
        await session.execute(select(Card).where(Card.tcgdex_id == data["tcgdex_id"]))
    ).scalar_one_or_none()

    if existing and existing.content_hash == data["content_hash"] and not force:
        return False, 0

    card = existing or Card(tcgdex_id=data["tcgdex_id"], language=settings.tcgdex_language)
    card.set_id = card_set.id
    card.local_id = data["local_id"]
    card.name = data["name"]
    card.category = data["category"]
    card.rarity = data["rarity"]
    card.illustrator = data["illustrator"]
    card.hp = data["hp"]
    card.types = data["types"]
    card.image_url = data["image_url"]
    card.raw = data["raw"]
    card.content_hash = data["content_hash"]
    card.synced_at = datetime.now().astimezone()

    if existing is None:
        session.add(card)
    await session.flush()

    written = 0
    known = {
        (v.type, v.subtype, v.size, v.stamp_key or ""): v
        for v in (
            await session.execute(select(CardVariant).where(CardVariant.card_id == card.id))
        ).scalars()
    }
    for row in normalize_variants(payload):
        key = (row["type"], row["subtype"], row["size"], row["stamp_key"])
        variant = known.get(key) or CardVariant(card_id=card.id)
        variant.tcgdex_variant_id = row["tcgdex_variant_id"] or variant.tcgdex_variant_id
        variant.type = row["type"]
        variant.subtype = row["subtype"]
        variant.size = row["size"]
        variant.stamp = row["stamp"]
        variant.stamp_key = row["stamp_key"]
        variant.is_normal = row["is_normal"]
        variant.is_holo = row["is_holo"]
        variant.is_reverse = row["is_reverse"]
        variant.is_first_edition = row["is_first_edition"]
        variant.is_promo = row["is_promo"]
        variant.label = row["label"]
        if key not in known:
            session.add(variant)
        written += 1
    await session.flush()
    return True, written


async def sync_cards(
    session: AsyncSession,
    *,
    set_ids: list[str] | None = None,
    refresh: bool = False,
    limit_sets: int | None = None,
    progress=None,
) -> SyncReport:
    report = SyncReport()

    async with TCGdexClient() as client:
        set_briefs = await client.list_sets()
        if set_ids:
            wanted = set(set_ids)
            set_briefs = [s for s in set_briefs if s.get("id") in wanted]
        if limit_sets:
            set_briefs = set_briefs[:limit_sets]

        report.sets_seen = len(set_briefs)
        log.info("sync.start", sets=len(set_briefs), refresh=refresh)

        for index, brief in enumerate(set_briefs, start=1):
            set_id = brief.get("id")
            if not set_id:
                continue
            try:
                set_payload = await client.get_set(set_id)
            except Exception as exc:  # noqa: BLE001 - one bad set must not stop the run
                report.errors.append(f"set {set_id}: {exc}")
                log.warning("sync.set_failed", set_id=set_id, error=str(exc))
                continue

            card_set, wrote = await _upsert_set(session, set_payload, force=refresh)
            report.sets_written += int(wrote)
            await session.commit()

            card_briefs = set_payload.get("cards") or []
            # On an incremental run, skip cards we already hold before spending a request.
            if not refresh and card_briefs:
                have = set(
                    (
                        await session.execute(
                            select(Card.tcgdex_id).where(
                                Card.tcgdex_id.in_([c["id"] for c in card_briefs if c.get("id")])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                report.cards_skipped += len(have)
                card_briefs = [c for c in card_briefs if c.get("id") not in have]

            for card_brief in card_briefs:
                card_id = card_brief.get("id")
                if not card_id:
                    continue
                report.cards_seen += 1
                try:
                    payload = await client.get_card(card_id)
                except Exception as exc:  # noqa: BLE001
                    report.errors.append(f"card {card_id}: {exc}")
                    continue

                try:
                    wrote_card, variants = await _upsert_card(
                        session, card_set, payload, force=refresh
                    )
                except Exception as exc:  # noqa: BLE001
                    # Roll back so the failed statement cannot poison every later card.
                    # A rollback that itself fails means the connection is gone, which is
                    # worth surfacing rather than looping through the rest of the set.
                    try:
                        await session.rollback()
                    except Exception as rollback_exc:  # noqa: BLE001
                        report.errors.append(f"card {card_id} rollback: {rollback_exc}")
                        raise
                    report.errors.append(f"card {card_id} write: {exc}")
                    continue

                if wrote_card:
                    report.cards_written += 1
                    report.variants_written += variants
                else:
                    report.cards_skipped += 1

                # Commit per card: interruption costs at most one card, never the run.
                await session.commit()
                if settings.tcgdex_request_delay:
                    await asyncio.sleep(settings.tcgdex_request_delay)

            if progress:
                await progress(index, len(set_briefs), set_id)
            log.info(
                "sync.set_done",
                set_id=set_id,
                index=index,
                total=len(set_briefs),
                cards_written=report.cards_written,
            )

    log.info("sync.done", **report.as_dict())
    return report
