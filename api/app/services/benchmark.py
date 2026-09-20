"""Run detection over every stored original and report how it did.

Real captures are the only honest test of a detector. Synthetic scenes have perfect corners,
even lighting and clean backgrounds; they catch regressions but they cannot tell you that a
Pokémon card back contains a pokéball whose bounding quad is card-shaped, or that a play mat's
aspect matches a sideways card. Those were both found by looking at actual photographs.

So every original the user has captured is a benchmark case. There is no ground truth for them,
but there are strong plausibility signals — a correctly detected card occupies a sensible share
of the frame, sits inside it, and is close to 88:63. This reports those per image so a change to
the detector can be measured against real data instead of argued about.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ImageKind, QualityVerdict
from app.imaging.geometry import CARD_ASPECT
from app.imaging.pipeline import process_capture
from app.logging_setup import get_logger
from app.models import Image, InventoryItem
from app.storage import get_storage

log = get_logger(__name__)

ORIGINALS = (ImageKind.ORIGINAL_FRONT, ImageKind.ORIGINAL_BACK)

# A card shot to fill the guide lands in this band. Outside it the detection is suspicious even
# when its own confidence is high: too small usually means something inside the card was found,
# too large usually means the mat or the frame.
PLAUSIBLE_AREA = (0.20, 0.80)
PLAUSIBLE_ASPECT_ERROR = 0.12


@dataclass
class Case:
    sku: str
    side: str
    detected: bool
    contains_card: bool | None = None
    method: str | None = None
    confidence: float = 0.0
    area_fraction: float = 0.0
    aspect: float = 0.0
    margin_px: float | None = None
    verdict: str | None = None
    px_per_mm: float | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        """Did the detector do the right thing on this image?

        For a labelled negative — a selfie, an empty frame — the right thing is to find
        nothing, so a non-detection counts as a pass rather than a failure.
        """
        if self.contains_card is False:
            return not self.detected or self.confidence < 0.55
        return self.plausible

    @property
    def plausible(self) -> bool:
        """Does this look like a correctly located card, on the numbers alone?"""
        if not self.detected:
            return False
        if not PLAUSIBLE_AREA[0] <= self.area_fraction <= PLAUSIBLE_AREA[1]:
            return False
        if self.margin_px is not None and self.margin_px <= 0:
            return False
        aspect_error = min(
            abs(self.aspect - CARD_ASPECT), abs(self.aspect - 1 / CARD_ASPECT)
        )
        return aspect_error <= PLAUSIBLE_ASPECT_ERROR

    def as_row(self) -> str:
        if self.contains_card is False:
            mark = "ok " if self.correct else "BAD"
        else:
            mark = "ok " if self.correct else "BAD"
        return (
            f"{mark} {self.sku:12} {self.side:5} "
            f"{'(no card) ' if self.contains_card is False else ''}"
            f"{self.method or '-':24} "
            f"conf={self.confidence:5.3f} area={self.area_fraction:5.3f} "
            f"aspect={self.aspect:5.3f} "
            f"margin={self.margin_px if self.margin_px is not None else 0:7.1f} "
            f"{self.verdict or '-'}"
        )


async def run(session: AsyncSession, *, verbose: bool = True) -> dict:
    storage = get_storage()
    rows = (
        (
            await session.execute(
                select(Image, InventoryItem)
                .join(InventoryItem, InventoryItem.id == Image.inventory_item_id)
                .where(Image.kind.in_(ORIGINALS))
                .order_by(InventoryItem.sku, Image.kind)
            )
        )
        .all()
    )

    cases: list[Case] = []
    for image, item in rows:
        side = "front" if image.kind is ImageKind.ORIGINAL_FRONT else "back"
        try:
            payload = storage.get(image.path)
        except FileNotFoundError:
            continue

        result = process_capture(payload)
        detection = result.detection
        quality = result.quality

        case = Case(
            sku=item.sku,
            side=side,
            detected=detection is not None,
            contains_card=item.contains_card,
            method=detection.method if detection else None,
            confidence=float(detection.confidence) if detection else 0.0,
            area_fraction=float(detection.area_fraction) if detection else 0.0,
            aspect=float(detection.aspect) if detection else 0.0,
            margin_px=quality.margin_px if quality else None,
            verdict=quality.verdict.value if quality else None,
            px_per_mm=quality.px_per_mm if quality else None,
            reasons=list(quality.reasons) if quality else [],
        )
        cases.append(case)
        if verbose:
            print(case.as_row())

    # Not every stored image contains a card. Three of the first ten captures were a selfie,
    # a blank frame and a photo of two people on a sofa — "not detected" is the right answer
    # there, and counting them as failures makes the detector look far worse than it is. They
    # are reported separately rather than silently excluded, because the benchmark cannot know
    # which is which and should not pretend to.
    correct = sum(1 for c in cases if c.correct)
    negatives = [c for c in cases if c.contains_card is False]
    positives = [c for c in cases if c.contains_card is not False]
    clean = sum(1 for c in cases if c.verdict == QualityVerdict.OK.value)
    summary = {
        "images": len(cases),
        "correct": correct,
        "rate": round(correct / len(cases), 3) if cases else 0.0,
        "with_card": len(positives),
        "with_card_correct": sum(1 for c in positives if c.correct),
        "negatives": len(negatives),
        "negatives_correct": sum(1 for c in negatives if c.correct),
        "verdict_ok": clean,
        "not_detected": sum(1 for c in cases if not c.detected),
    }
    if verbose:
        print()
        print(
            f"{summary['with_card_correct']}/{summary['with_card']} images containing a card "
            f"located correctly; {summary['negatives_correct']}/{summary['negatives']} "
            f"labelled negatives correctly rejected."
        )
        print(f"overall {correct}/{len(cases)} ({summary['rate']:.0%})")
        print(
            "Unlabelled images are assumed to contain a card. Label the exceptions with "
            "`make mark ARGS=\"CARD-000005 --no-card\"`."
        )
    return summary
