"""Judge whether a capture is good enough to grade a card from.

Bad input does not announce itself downstream — a soft photo quietly invents scuffing, a blown
highlight quietly hides surface wear. Catching it at ingest is far cheaper than discovering it
after 500 cards have been graded.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from app.config import settings
from app.enums import QualityVerdict

# Below this many pixels of clearance, treat the card as cut off by the frame.
MIN_FRAME_MARGIN_PX = 8.0

# A card covering less of the frame than this is either badly framed or is not the card at all.
# Pokémon backs in particular contain a large circular pokéball whose bounding quad is very
# nearly card-shaped, so a mis-detection here looks entirely plausible on its own numbers.
MIN_AREA_FRACTION = 0.18


@dataclass(frozen=True)
class QualityReport:
    verdict: QualityVerdict
    blur_score: float
    clipped_highlights: float
    crushed_shadows: float
    glare_fraction: float
    px_per_mm: float | None
    margin_px: float | None = None
    area_fraction: float | None = None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "blur_score": round(self.blur_score, 2),
            "clipped_highlights": round(self.clipped_highlights, 4),
            "crushed_shadows": round(self.crushed_shadows, 4),
            "glare_fraction": round(self.glare_fraction, 4),
            "px_per_mm": round(self.px_per_mm, 2) if self.px_per_mm else None,
            "margin_px": round(self.margin_px, 1) if self.margin_px is not None else None,
            "area_fraction": round(self.area_fraction, 3)
            if self.area_fraction is not None
            else None,
            "reasons": self.reasons,
        }


# Every blur score is measured at this width, whatever the capture's own resolution.
#
# Variance of the Laplacian is strongly scale-dependent: the kernel spans three pixels, so on a
# high-resolution image those three pixels cover a fraction of a millimetre of card, where
# neighbouring values are nearly identical and the variance collapses. Measured on one card at
# two sizes: the identical content scored 30 at 2205x3080 and 1305 at 600x825, a ~43x swing.
#
# Applying one fixed threshold across capture resolutions is therefore meaningless, and it was
# not merely imprecise but backwards: at native resolution the pristine TCGdex reference art for
# me04-102 scored 30.1 while our photograph of that same card scored 35.1. The gate rejected the
# reference image it exists to compare against, and flagged all twenty real captures as soft.
#
# 600 px is the width of TCGdex's own art, which makes the reference and a capture directly
# comparable — the one comparison worth being able to make.
BLUR_REFERENCE_WIDTH = 600


def blur_score(grey: np.ndarray) -> float:
    """Variance of the Laplacian at a fixed reference width. Higher is sharper.

    Resolution-normalised so one threshold means the same thing for a 1080p webcam frame and a
    12 MP phone capture. Never upscales: enlarging cannot add detail, and interpolation inflates
    the score of a genuinely soft image.
    """
    if grey.shape[1] > BLUR_REFERENCE_WIDTH:
        height = max(1, round(grey.shape[0] * BLUR_REFERENCE_WIDTH / grey.shape[1]))
        grey = cv2.resize(
            grey, (BLUR_REFERENCE_WIDTH, height), interpolation=cv2.INTER_AREA
        )
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def exposure(grey: np.ndarray) -> tuple[float, float]:
    """Fractions of the image that are blown out and crushed."""
    total = grey.size
    if total == 0:
        return 0.0, 0.0
    return (
        float(np.count_nonzero(grey >= 250) / total),
        float(np.count_nonzero(grey <= 5) / total),
    )


def glare_fraction(grey: np.ndarray) -> float:
    """Fraction covered by sizeable near-white specular blobs.

    Distinguished from a legitimately bright card by requiring connected regions of real size —
    scattered bright pixels are artwork, a large blob is a reflection. The fix for a high value
    is cross-polarisation on the capture rig, not more software.
    """
    _, mask = cv2.threshold(grey, 246, 255, cv2.THRESH_BINARY)
    if not np.any(mask):
        return 0.0

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    total = grey.size
    min_blob = max(64, total // 5000)

    blown = sum(
        stats[i, cv2.CC_STAT_AREA]
        for i in range(1, count)
        if stats[i, cv2.CC_STAT_AREA] >= min_blob
    )
    return float(blown / total)


def frame_margin(corners: np.ndarray, width: int, height: int) -> float:
    """Smallest distance from any detected corner to the frame edge, in pixels."""
    points = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    return float(
        min(
            points[:, 0].min(),
            points[:, 1].min(),
            width - points[:, 0].max(),
            height - points[:, 1].max(),
        )
    )


def assess_quality(
    image: np.ndarray,
    px_per_mm: float | None = None,
    margin_px: float | None = None,
    area_fraction: float | None = None,
) -> QualityReport:
    """Grade a capture. `px_per_mm` is the SOURCE scale, not the rectified one."""
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image

    blur = blur_score(grey)
    clipped, crushed = exposure(grey)
    glare = glare_fraction(grey)

    reasons: list[str] = []
    verdict = QualityVerdict.OK

    # A card touching the frame edge has been cropped by it. There is then no visible border
    # on that side to locate, so the crop cannot be exact however good the detector is — and
    # the advice to fill the frame makes this a very easy mistake to make.
    if area_fraction is not None and 0 < area_fraction < MIN_AREA_FRACTION:
        reasons.append(
            f"the detected card covers only {area_fraction:.0%} of the frame — either it was "
            "shot from too far away, or something inside the card was detected instead of the "
            "card. Check the crop"
        )
        verdict = _worse(verdict, QualityVerdict.REVIEW)

    if margin_px is not None and margin_px < MIN_FRAME_MARGIN_PX:
        reasons.append(
            f"the card reaches within {margin_px:.0f}px of the frame edge and is probably "
            "cropped — leave a small gap all the way round so every border is visible"
        )
        verdict = _worse(verdict, QualityVerdict.REVIEW)

    if px_per_mm is not None and px_per_mm < settings.quality_min_px_per_mm:
        reasons.append(
            f"resolution {px_per_mm:.1f} px/mm is below the {settings.quality_min_px_per_mm:.0f} "
            "needed to measure a 2.5mm² defect"
        )
        verdict = QualityVerdict.REJECT

    if blur < settings.quality_blur_min:
        reasons.append(f"soft focus (blur score {blur:.0f} < {settings.quality_blur_min:.0f})")
        # Softness is recoverable by reshooting, so it asks for review rather than rejecting.
        verdict = _worse(verdict, QualityVerdict.REVIEW)

    if clipped > settings.quality_max_clipped_fraction:
        reasons.append(f"{clipped:.1%} of the frame is blown out, which hides surface wear")
        verdict = _worse(verdict, QualityVerdict.REVIEW)

    if crushed > settings.quality_max_clipped_fraction:
        reasons.append(f"{crushed:.1%} of the frame is crushed, which hides edge whitening")
        verdict = _worse(verdict, QualityVerdict.REVIEW)

    if glare > settings.quality_max_glare_fraction:
        reasons.append(f"{glare:.1%} specular glare; consider cross-polarised lighting")
        verdict = _worse(verdict, QualityVerdict.REVIEW)

    return QualityReport(
        verdict=verdict,
        blur_score=blur,
        clipped_highlights=clipped,
        crushed_shadows=crushed,
        glare_fraction=glare,
        px_per_mm=px_per_mm,
        margin_px=margin_px,
        area_fraction=area_fraction,
        reasons=reasons,
    )


_ORDER = [QualityVerdict.OK, QualityVerdict.REVIEW, QualityVerdict.REJECT]


def _worse(a: QualityVerdict, b: QualityVerdict) -> QualityVerdict:
    return a if _ORDER.index(a) >= _ORDER.index(b) else b
