"""Decide whether a quad is really the card's outer boundary — without ground truth.

Every heuristic so far could be satisfied by the wrong thing. A pokéball is card-shaped, an
inner frame is card-shaped and card-sized, a play mat is card-shaped and merely bigger. Aspect,
area and fill cannot separate them, and each rule that tried was eventually wrong somewhere.

There is one property only the true boundary has: **the card is on one side of it and the
background on the other**. Sample a thin ring just inside the quad and another just outside it.
For the card's outer edge those two rings differ sharply and the outer ring is uniform, because
it is all mat. For an inner frame both rings are card, so they barely differ. For the mat's own
edge the inner ring contains background, so it is not uniform.

That is measurable from the image alone, which makes it both a ranking signal and an honest
accuracy metric for the benchmark.
"""

from dataclasses import dataclass

import cv2
import numpy as np

# Ring thickness as a fraction of the card's short side. Thin enough to sit on the boundary,
# thick enough to average out noise and the mat's texture.
RING_FRACTION = 0.035

# Gap between the quad and the outer ring, so a slightly loose crop is not scored as if the
# ring were still on the card.
GAP_FRACTION = 0.012


@dataclass(frozen=True)
class BorderEvidence:
    inside_mean: float
    outside_mean: float
    contrast: float          # |inside - outside| in Lab, normalised
    outside_uniformity: float  # 1.0 when the outer ring is a single flat colour
    score: float

    def as_dict(self) -> dict:
        return {
            "contrast": round(self.contrast, 3),
            "outside_uniformity": round(self.outside_uniformity, 3),
            "border_score": round(self.score, 3),
        }


def _ring_mask(shape: tuple[int, int], quad: np.ndarray, scale: float) -> np.ndarray:
    """Filled polygon of the quad scaled about its own centre."""
    centre = quad.mean(axis=0)
    scaled = (centre + (quad - centre) * scale).astype(np.int32)
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.fillConvexPoly(mask, scaled, 255)
    return mask


def border_evidence(image: np.ndarray, quad: np.ndarray) -> BorderEvidence:
    """How strongly the image agrees that `quad` separates card from background."""
    corners = np.asarray(quad, dtype=np.float32).reshape(4, 2)
    height, width = image.shape[:2]
    shape = (height, width)

    # Work in Lab: it separates a dark blue card from a black mat far better than luminance,
    # which is exactly the case that defeated the edge strategies.
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)

    inner_outer = _ring_mask(shape, corners, 1.0 - GAP_FRACTION)
    inner_inner = _ring_mask(shape, corners, 1.0 - GAP_FRACTION - RING_FRACTION)
    inside_ring = cv2.subtract(inner_outer, inner_inner)

    outer_outer = _ring_mask(shape, corners, 1.0 + GAP_FRACTION + RING_FRACTION)
    outer_inner = _ring_mask(shape, corners, 1.0 + GAP_FRACTION)
    outside_ring = cv2.subtract(outer_outer, outer_inner)

    if np.count_nonzero(inside_ring) < 50 or np.count_nonzero(outside_ring) < 50:
        return BorderEvidence(0.0, 0.0, 0.0, 0.0, 0.0)

    inside = lab[inside_ring > 0]
    outside = lab[outside_ring > 0]

    inside_mean = inside.mean(axis=0)
    outside_mean = outside.mean(axis=0)

    # Lab distance, normalised by a difference big enough to be unambiguous.
    contrast = float(np.linalg.norm(inside_mean - outside_mean)) / 60.0
    contrast = min(1.0, contrast)

    # A mat is uniform; a ring still on the card's artwork is not.
    spread = float(np.mean(outside.std(axis=0)))
    uniformity = max(0.0, 1.0 - spread / 40.0)

    return BorderEvidence(
        inside_mean=float(inside_mean[0]),
        outside_mean=float(outside_mean[0]),
        contrast=contrast,
        outside_uniformity=uniformity,
        score=contrast * (0.45 + 0.55 * uniformity),
    )
