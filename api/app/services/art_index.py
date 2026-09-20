"""Build and query the perceptual-hash index over the card catalogue."""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models import Card, CardSet
from app.services.hashing import fetch_art, grid_from_bytes, phash_from_bytes

log = get_logger(__name__)

# Postgres has no unsigned 64-bit integer, so hashes are stored signed and converted here.
SIGN_BIT = 1 << 63
UNSIGNED_MASK = (1 << 64) - 1

CONCURRENCY = 8


def to_signed(value: int) -> int:
    return value - (1 << 64) if value >= SIGN_BIT else value


def to_unsigned(value: int) -> int:
    return value & UNSIGNED_MASK


@dataclass
class IndexReport:
    seen: int = 0
    hashed: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = None

    def as_dict(self) -> dict:
        return {
            "seen": self.seen,
            "hashed": self.hashed,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": (self.errors or [])[:10],
            "error_count": len(self.errors or []),
        }


async def build_index(
    session: AsyncSession,
    *,
    limit: int | None = None,
    set_id: str | None = None,
    refresh: bool = False,
) -> dict:
    """Hash every card's art. Resumable: already-hashed cards are skipped unless `refresh`.

    Failures are recorded on the card rather than dropped, so a re-run retries exactly the
    cards that need it instead of the whole catalogue.
    """
    report = IndexReport(errors=[])

    query = select(Card).where(Card.image_url.is_not(None))
    if set_id:
        query = query.join(CardSet, CardSet.id == Card.set_id).where(CardSet.tcgdex_id == set_id)
    if not refresh:
        # Cards hashed before the grid hash existed still need it.
        query = query.where(
            (Card.art_phash.is_(None)) | (Card.art_grid_hash.is_(None))
        )
    query = query.order_by(Card.tcgdex_id)
    if limit:
        query = query.limit(limit)

    cards = (await session.execute(query)).scalars().all()
    report.seen = len(cards)

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        # Bounded concurrency: enough to keep the network busy, gentle enough not to hammer a
        # free public API that this project depends on.
        batch: list[Card] = []
        for card in cards:
            batch.append(card)
            if len(batch) < CONCURRENCY:
                continue
            await _hash_batch(client, session, batch, report)
            # Commit per batch, not once at the end. Hashing the English catalogue takes ten
            # minutes; committing only on completion means an interruption at minute nine
            # discards everything and the whole run starts again.
            await session.commit()
            batch = []
        if batch:
            await _hash_batch(client, session, batch, report)

    await session.commit()
    invalidate_grid_cache()
    log.info("art_index.done", **{k: v for k, v in report.as_dict().items() if k != "errors"})
    return report.as_dict()


async def _hash_batch(client, session: AsyncSession, cards: list[Card], report: IndexReport):
    import asyncio

    payloads = await asyncio.gather(
        *(fetch_art(client, card.image_url, "low") for card in cards)
    )
    for card, payload in zip(cards, payloads, strict=True):
        if payload is None:
            card.art_hash_error = "art could not be fetched"
            report.failed += 1
            report.errors.append(f"{card.tcgdex_id}: fetch failed")
            continue
        value = phash_from_bytes(payload)
        if value is None:
            card.art_hash_error = "art could not be decoded"
            report.failed += 1
            report.errors.append(f"{card.tcgdex_id}: decode failed")
            continue
        card.art_phash = to_signed(value)
        card.art_grid_hash = grid_from_bytes(payload)
        card.art_hashed_at = datetime.now(UTC)
        card.art_hash_error = None
        report.hashed += 1
    await session.flush()


@dataclass(frozen=True)
class Candidate:
    tcgdex_id: str
    name: str
    set_name: str
    distance: int
    image_url: str | None
    # The printed collector number ("4" in 4/102). Carried so fusion can check an OCR reading
    # against the candidate without a second round trip to the database.
    local_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "tcgdex_id": self.tcgdex_id,
            "name": self.name,
            "set": self.set_name,
            "distance": self.distance,
            "local_id": self.local_id,
        }


async def nearest(session: AsyncSession, value: int, limit: int = 12) -> list[Candidate]:
    """Cards whose art is closest to `value` by Hamming distance.

    Computed in Postgres so the catalogue never has to be pulled into Python. At 20,000 cards a
    sequential popcount is a few milliseconds — an index that could prune this exists (BK-tree,
    or splitting the hash into indexed chunks) but would be optimising something already far
    below the per-card time budget.
    """
    signed = to_signed(value)
    rows = (
        await session.execute(
            select(
                Card.tcgdex_id,
                Card.name,
                CardSet.name,
                Card.image_url,
                Card.local_id,
                # length(replace(...)) is portable; bit_count() needs Postgres 14+, which the
                # pinned postgres:16 image has, so use it.
                func_bit_count(Card.art_phash, signed).label("distance"),
            )
            .join(CardSet, CardSet.id == Card.set_id)
            .where(Card.art_phash.is_not(None))
            .order_by("distance")
            .limit(limit)
        )
    ).all()
    return [
        Candidate(
            tcgdex_id=r[0],
            name=r[1],
            set_name=r[2],
            image_url=r[3],
            local_id=r[4],
            distance=int(r[5]),
        )
        for r in rows
    ]


def func_bit_count(column, value: int):
    """Hamming distance as a SQL expression: popcount of the XOR.

    Postgres' `bit_count` accepts `bit` and `bytea`, not `bigint`, so the XOR is cast to a
    64-bit string first. Doing this in SQL keeps the catalogue in the database — at 20,000
    cards a sequential popcount is a few milliseconds, far below the per-card time budget.
    """
    from sqlalchemy import cast, func
    from sqlalchemy.dialects.postgresql import BIT

    return func.bit_count(cast(column.op("#")(value), BIT(64)))


# --- Grid-hash search -------------------------------------------------------------------
#
# Held in memory rather than searched in SQL. The whole catalogue is 128 bytes per card — under
# 3MB for 21,775 cards — and a numpy XOR-and-popcount over that takes about 40ms, which is
# faster than round-tripping the comparison into Postgres and needs no extension. It also keeps
# the door open for a better descriptor later without a schema change.

_POPCOUNT = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(1).astype(np.uint16)

_GRID_IDS: list[str] | None = None
_GRID_TABLE: np.ndarray | None = None
_GRID_META: dict[str, tuple[str, str, str | None]] = {}


def invalidate_grid_cache() -> None:
    """Drop the cached table. Call after re-indexing."""
    global _GRID_IDS, _GRID_TABLE
    _GRID_IDS, _GRID_TABLE = None, None
    _GRID_META.clear()


async def _load_grid(session: AsyncSession) -> None:
    global _GRID_IDS, _GRID_TABLE
    rows = (
        await session.execute(
            select(
                Card.tcgdex_id,
                Card.art_grid_hash,
                Card.name,
                CardSet.name,
                Card.image_url,
                Card.local_id,
            )
            .join(CardSet, CardSet.id == Card.set_id)
            .where(Card.art_grid_hash.is_not(None))
        )
    ).all()
    if not rows:
        _GRID_IDS, _GRID_TABLE = [], None
        return
    _GRID_IDS = [r[0] for r in rows]
    _GRID_TABLE = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.uint8).reshape(
        len(rows), -1
    )
    for row in rows:
        _GRID_META[row[0]] = (row[2], row[3], row[4], row[5])
    log.info("art_index.grid_loaded", cards=len(_GRID_IDS))


async def nearest_by_grid(
    session: AsyncSession, grid: bytes, limit: int = 12
) -> list[Candidate]:
    """Cards whose spatial grid hash is closest to `grid`.

    Measured against the single perceptual hash on twelve real captures: rank-1 recall went
    from 7/12 to 12/12, and both cards that the single hash pushed outside the top 20 — where
    registration would never have seen them — came first.
    """
    if _GRID_TABLE is None or _GRID_IDS is None:
        await _load_grid(session)
    if _GRID_TABLE is None or not _GRID_IDS:
        return []

    query = np.frombuffer(grid, dtype=np.uint8)
    if query.shape[0] != _GRID_TABLE.shape[1]:
        return []

    distances = _POPCOUNT[np.bitwise_xor(_GRID_TABLE, query)].sum(axis=1)
    order = np.argsort(distances)[:limit]

    results: list[Candidate] = []
    for index in order:
        tcgdex_id = _GRID_IDS[int(index)]
        name, set_name, image_url, local_id = _GRID_META[tcgdex_id]
        results.append(
            Candidate(
                tcgdex_id=tcgdex_id,
                name=name,
                set_name=set_name,
                image_url=image_url,
                local_id=local_id,
                distance=int(distances[index]),
            )
        )
    return results
