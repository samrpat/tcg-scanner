"""On-disk cache for reference art.

Recognition fetches card art from TCGdex and throws it away. That is tolerable on a laptop with
a fast connection and invisible against everything else; on a Raspberry Pi it is the dominant
cost. Measured on this machine: one card that failed to identify spent **137 seconds** in
recognition, almost all of it re-downloading art the machine had already seen — and the Pi has
less CPU, less bandwidth, and is meant to run for hours unattended.

The art never changes. A card's picture is fixed once printed, so the only correct number of
times to download it is once.

Stored as the original bytes rather than decoded arrays: JPEG is an order of magnitude smaller
than the raw pixels, and decode is cheap next to the network round trip that this replaces.
Roughly 40 KB per card at `low` quality, so a full 23,544-card catalogue would be about 1 GB if
every card were ever consulted — and in practice only the ones actually scanned are.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np

from app.config import settings
from app.logging_setup import get_logger

log = get_logger(__name__)


def _root() -> Path:
    return Path(settings.storage_local_root).parent / "art-cache"


def _path_for(url: str) -> Path:
    """Two-level fan-out on the hash, so no directory holds tens of thousands of entries.

    Keyed on the full URL, which already carries the card id and the quality suffix, so `low`
    and `high` of the same card cannot collide.
    """
    digest = hashlib.sha256(url.encode()).hexdigest()
    return _root() / digest[:2] / f"{digest}.jpg"


def get(url: str) -> np.ndarray | None:
    """Decoded art for this URL if it has been fetched before, else None."""
    path = _path_for(url)
    try:
        if not path.exists():
            return None
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        art = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return art if art is not None and art.size else None
    except Exception:  # noqa: BLE001 - a damaged cache entry must fall back to the network
        log.warning("art_cache.read_failed", url=url)
        return None


def put(url: str, payload: bytes) -> None:
    """Store the fetched bytes. Never raises: a cache is an optimisation, not a dependency."""
    path = _path_for(url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a crash mid-write cannot leave a truncated file that later
        # decodes to a corrupt image and quietly poisons every future match for that card.
        temporary = path.with_suffix(".part")
        temporary.write_bytes(payload)
        temporary.replace(path)
    except Exception:  # noqa: BLE001
        log.warning("art_cache.write_failed", url=url)


def stats() -> dict:
    """Entry count and size on disk, for /health/detail."""
    root = _root()
    if not root.exists():
        return {"entries": 0, "bytes": 0}
    entries = 0
    total = 0
    for path in root.rglob("*.jpg"):
        entries += 1
        total += path.stat().st_size
    return {"entries": entries, "bytes": total}


async def warm(session, set_id: str | None = None, limit: int | None = None) -> dict:
    """Pre-download reference art so recognition never waits on the network.

    Recognition fetching art live is fine on a laptop and wrong in production. It makes every
    identification depend on a third party being reachable and fast, and when TCGdex stopped
    answering this machine — after the set-prior fallback issued several hundred requests per
    unidentified card — identification stopped working entirely rather than degrading.

    Run this once per set before scanning it. Deliberately serial with a small delay: the whole
    reason the source became unreachable was request volume, and a pre-warm has no deadline.
    """
    import asyncio

    import httpx
    from sqlalchemy import select

    from app.models import Card, CardSet
    from app.services.recognition import ART_QUALITY

    query = select(Card.image_url).where(Card.image_url.is_not(None))
    if set_id:
        query = query.join(CardSet, CardSet.id == Card.set_id).where(
            CardSet.tcgdex_id == set_id
        )
    if limit:
        query = query.limit(limit)
    urls = [f"{u}/{ART_QUALITY}.jpg" for u in (await session.execute(query)).scalars().all()]

    fetched = cached = failed = 0
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for url in urls:
            if get(url) is not None:
                cached += 1
                continue
            try:
                response = await client.get(url)
                response.raise_for_status()
                put(url, response.content)
                fetched += 1
            except Exception:  # noqa: BLE001
                failed += 1
            # Be a good citizen. This is what the runtime path failed to be.
            await asyncio.sleep(0.05)

    return {
        "considered": len(urls),
        "already_cached": cached,
        "fetched": fetched,
        "failed": failed,
        **stats(),
    }
