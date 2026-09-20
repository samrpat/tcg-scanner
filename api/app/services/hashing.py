"""Perceptual hashes for the card catalogue.

Comparing a capture against twenty thousand card images directly is not viable on a Pi. A
perceptual hash is: 64 bits per card, computed once, compared by Hamming distance in a single
indexed query.

Storing the *hash* rather than the image is the point. A full art mirror at TCGdex's `high`
resolution is roughly 2GB for the English catalogue; the hashes are under a megabyte. High-res
art is fetched on demand, for the handful of candidates that reach registration.
"""

import asyncio
from dataclasses import dataclass

import cv2
import numpy as np

from app.logging_setup import get_logger

log = get_logger(__name__)

# 8x8 DCT low-frequency block -> 64 bits. The classic pHash size: small enough to compare with a
# popcount, large enough that unrelated cards are far apart.
HASH_SIDE = 8
DCT_SIDE = 32


def phash(image: np.ndarray) -> int:
    """Perceptual hash of an image, as a 64-bit integer.

    DCT-based rather than average-based: it survives the brightness and contrast differences
    between a studio scan and a phone photo under a lamp, which is exactly the comparison being
    made here.
    """
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    small = cv2.resize(grey, (DCT_SIDE, DCT_SIDE), interpolation=cv2.INTER_AREA)
    frequencies = cv2.dct(small.astype(np.float32))

    block = frequencies[:HASH_SIDE, :HASH_SIDE].flatten()
    # Skip the DC term when taking the median: it carries overall brightness, which is exactly
    # what the hash should ignore.
    median = float(np.median(block[1:]))

    bits = 0
    for index, value in enumerate(block):
        if value > median:
            bits |= 1 << index
    return bits


def hamming(a: int, b: int) -> int:
    return int(a ^ b).bit_count()


# Cells per side of the spatial grid. A single 64-bit hash summarises the whole card, which
# averages away *where* things are — and cards differ mostly by layout, so two unrelated cards
# with similar overall tone land close together. Hashing each cell of a 4x4 grid keeps that
# spatial information: 16 hashes, 1024 bits, 128 bytes per card.
#
# Measured against the plain hash on twelve real captures: rank-1 recall went from 7/12 to
# 12/12, and a card the single hash buried at rank 445 came first.
GRID = 4


def grid_phash(image: np.ndarray) -> bytes:
    """Perceptual hash per cell of a GRID x GRID lattice, packed for storage."""
    height, width = image.shape[:2]
    values = []
    for row in range(GRID):
        for column in range(GRID):
            cell = image[
                row * height // GRID : (row + 1) * height // GRID,
                column * width // GRID : (column + 1) * width // GRID,
            ]
            values.append(phash(cell))
    return b"".join(v.to_bytes(8, "big") for v in values)


def grid_from_bytes(payload: bytes) -> bytes | None:
    buffer = np.frombuffer(payload, dtype=np.uint8)
    if buffer.size == 0:
        return None
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        return None
    return grid_phash(image)


@dataclass(frozen=True)
class HashedArt:
    tcgdex_id: str
    phash: int
    width: int
    height: int


def phash_from_bytes(payload: bytes) -> int | None:
    buffer = np.frombuffer(payload, dtype=np.uint8)
    if buffer.size == 0:
        return None
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        return None
    return phash(image)


async def fetch_art(client, image_url: str, quality: str = "low") -> bytes | None:
    """Fetch a card's art. TCGdex serves `<url>/low.jpg` and `<url>/high.jpg`.

    `low` (~22KB) is ample for a coarse prefilter; `high` (~98KB) is reserved for registration,
    where the extra detail actually buys feature matches.
    """
    try:
        response = await client.get(f"{image_url}/{quality}.jpg")
        response.raise_for_status()
        return response.content
    except Exception as exc:  # noqa: BLE001 - one unavailable image must not stop a sync
        log.warning("art.fetch_failed", url=image_url, quality=quality, error=str(exc))
        return None


async def gather_limited(coros, limit: int):
    """Run coroutines with bounded concurrency, preserving order."""
    semaphore = asyncio.Semaphore(limit)

    async def guarded(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*(guarded(c) for c in coros))
