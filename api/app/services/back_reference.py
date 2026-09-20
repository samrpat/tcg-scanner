"""Build the card-back reference by consensus from real captures.

Every English Pokémon card shares one back, but no public source publishes a clean scan of it —
TCGdex has no back image. So the reference has to come from the captures themselves, and a
single capture is a poor choice: it carries that photograph's glare, white balance and whatever
the mat reflected.

Registering many backs into a common frame and taking the per-pixel **median** removes all of
that. Glare lands in different places on different cards, so the median rejects it; lighting
differences average out; a scratch on one card is outvoted by nine clean ones. The result is a
cleaner reference than any of its inputs, and it improves as more cards are scanned.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ImageKind
from app.imaging.backref import REFERENCE_PATH, register_back
from app.imaging.geometry import order_corners
from app.logging_setup import get_logger
from app.models import Image
from app.storage import get_storage

log = get_logger(__name__)

# The reference is rendered larger than it is used, so ORB has real detail to key on and the
# reference does not become the limiting factor in registration accuracy.
REFERENCE_SIZE = (630, 880)

# Below this many contributing captures the median has too few votes to reject glare reliably,
# and rebuilding would risk making the reference worse than the one already shipped.
MIN_CONTRIBUTORS = 4


# Scales searched when locating the card's true edge. A reference cut from a real capture can
# sit a few percent inside or outside the card, so the search has to cover both.
EDGE_SEARCH = np.arange(0.90, 1.16, 0.005)


def _snap_to_card_edge(photo: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Scale the quad about its centre to wherever card meets background most sharply."""
    from app.imaging.verify import border_evidence

    centre = corners.mean(axis=0)
    best, best_scale = -1.0, 1.0
    for scale in EDGE_SEARCH:
        candidate = (centre + (corners - centre) * scale).astype(np.float32)
        score = border_evidence(photo, candidate).score
        if score > best:
            best, best_scale = score, float(scale)
    return (centre + (corners - centre) * best_scale).astype(np.float32)


@dataclass
class BuildReport:
    considered: int = 0
    registered: int = 0
    written: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "registered": self.registered,
            "written": self.written,
            "reason": self.reason,
        }


async def rebuild(session: AsyncSession, *, dry_run: bool = False) -> dict:
    """Rebuild the back reference from every stored original back."""
    report = BuildReport()
    storage = get_storage()

    backs = (
        (
            await session.execute(
                select(Image).where(Image.kind == ImageKind.ORIGINAL_BACK, Image.error.is_(None))
            )
        )
        .scalars()
        .all()
    )
    report.considered = len(backs)

    width, height = REFERENCE_SIZE
    destination = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])

    layers: list[np.ndarray] = []
    for image in backs:
        photo = cv2.imdecode(
            np.frombuffer(storage.get(image.path), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if photo is None:
            continue
        registration = register_back(photo)
        if registration is None:
            continue

        # Anchor to the card's true edge before warping, rather than to wherever the current
        # reference happens to sit. Building the reference from un-anchored corners bakes the
        # old reference's error into the new one — the calibration then chases its own tail,
        # which is exactly what happened on the first attempt.
        corners = _snap_to_card_edge(photo, order_corners(registration.corners))
        matrix = cv2.getPerspectiveTransform(corners.astype(np.float32), destination)
        layers.append(
            cv2.warpPerspective(photo, matrix, (width, height), flags=cv2.INTER_CUBIC)
        )

    report.registered = len(layers)
    if len(layers) < MIN_CONTRIBUTORS:
        report.reason = (
            f"only {len(layers)} captures registered; need {MIN_CONTRIBUTORS} for the median "
            "to outvote glare"
        )
        return report.as_dict()

    # Median, not mean: glare is a bright outlier in a few frames, and a mean would smear it
    # across the reference instead of discarding it.
    consensus = np.median(np.stack(layers), axis=0).astype(np.uint8)

    if dry_run:
        report.reason = "dry run"
        return report.as_dict()

    REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(REFERENCE_PATH), consensus, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    report.written = True
    report.reason = f"median of {len(layers)} registered backs"
    log.info("back_reference.rebuilt", contributors=len(layers))
    return report.as_dict()
