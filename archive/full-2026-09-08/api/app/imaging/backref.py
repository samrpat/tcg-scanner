"""Locate a card back by registering it against a reference image.

Every English Pokémon card has the identical back. That turns locating one from a *detection*
problem into a *registration* problem, and registration is strictly better here:

- Edge detection has to guess which of several concentric rectangles is the card. Registration
  knows, because the reference tells it where the card's corners are relative to the artwork.
- Edge detection cannot tell 0° from 180°, and gets 90° wrong when it locks onto a landscape
  sub-region. Registration recovers orientation from the artwork itself.
- Four corners fitted independently can come out skewed. A homography from hundreds of matched
  features is over-determined and lands square.

ORB rather than SIFT: free of patent concerns, fast enough to run per capture on a Pi, and the
Pokémon back is high-contrast line art, which is exactly what corner-based features like.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

REFERENCE_PATH = Path(__file__).parent / "assets" / "pokemon-back.jpg"

# Registration runs on a downscaled copy: ORB is scale-tolerant and a 12MP photo is mostly
# wasted work here.
WORK_MAX_DIM = 1100

# Brute-force Hamming matching is quadratic in feature count, and 3000x3000 dominated the
# whole pipeline — it took the test suite from 5s to 83s. 1200 still yields several hundred
# inliers on a card back, which is far more than the homography needs.
MAX_FEATURES = 1200
# Lowe's ratio test. Card backs contain repeated motifs (the logo appears twice), so this needs
# to be strict or the repeats generate confident nonsense.
RATIO = 0.72

MIN_MATCHES = 18
MIN_INLIERS = 14
MIN_INLIER_RATIO = 0.32

# Sanity bounds on the recovered quad, same spirit as the detector's.
MIN_AREA_FRACTION = 0.05
MAX_AREA_FRACTION = 0.95
ASPECT_TOLERANCE = 0.30

# The reference was cut from a real capture, so its own crop sits slightly inside the card.
# Measured, not guessed: sweeping the expansion against border evidence across ten independent
# card backs peaks at 1.04, with nine of the ten agreeing to within 0.01. Line-fitting the true
# border on the one back where the fitter succeeded independently gave 1.029.
#
# It matters because edgewear is measured in the outermost millimetre. Replace the reference
# with an exactly-cropped scan and this becomes 1.0.
# The reference is the card back's INNER ARTWORK PANEL, not the card. Registration locks onto
# the panel because that is where the features are — the blue border is a flat colour with
# nothing to match. Projecting the reference corners therefore lands inside the card, cutting
# off the border where edge wear is measured.
#
# Calibrated by eye rather than by metric, deliberately. Three automated attempts all failed for
# the same underlying reason: a dark blue border against a black mat has almost no luminance
# contrast and its chroma step sits at the *inner* edge of the border, so both edge fitting and
# colour segmentation stop at the panel instead of crossing to the card edge. Rendering the crop
# at a range of expansions and looking settled it in one pass: 1.00 shows no border, 1.11 shows
# the full border with the card edge at the frame, 1.14 starts including mat.
#
# Biased marginally outward: a sliver of mat costs nothing, while clipping the border loses the
# very pixels edge wear is measured on.
REFERENCE_EXPANSION = 1.12

# The reference image is itself very slightly rotated relative to the card's true axes. It was
# built by median-blending captures that were each registered against an earlier reference, so a
# small rotation propagated through the bootstrap and froze into the consensus. An axis-aligned
# corner box in reference coordinates is therefore aligned to the REFERENCE, not to the card:
# dewarping it squares the reference and leaves the card content tilted by the same amount.
#
# Measured, not guessed. Signed tilt of the long Hough lines across all 12 listing renders:
#   fronts (TCGdex art, known-square reference):  median +0.00 deg, sd 0.22
#   backs  (this reference):                      median +0.64 deg, sd 0.23, ALL 12 positive
# A constant offset with the spread of the measurement itself is a reference-alignment error,
# not noise, so it cancels with a constant. Rotating the sampling box by this much about the
# reference centre re-aligns it to the card before projection.
#
# Replace the reference with a squarely-cropped scan and this becomes 0.0, exactly as
# REFERENCE_EXPANSION becomes 1.0.
REFERENCE_ROTATION_DEG = 0.64


@dataclass(frozen=True)
class Registration:
    corners: np.ndarray
    inliers: int
    matches: int
    inlier_ratio: float
    rotation_hint: float  # degrees the reference was rotated by, from the homography

    def as_dict(self) -> dict:
        return {
            "inliers": self.inliers,
            "matches": self.matches,
            "inlier_ratio": round(self.inlier_ratio, 3),
            "rotation_deg": round(self.rotation_hint, 1),
        }


@lru_cache(maxsize=1)
def _reference() -> tuple[np.ndarray, tuple, np.ndarray] | None:
    """Reference image, its ORB keypoints and descriptors. Computed once per process."""
    if not REFERENCE_PATH.exists():
        return None
    image = cv2.imread(str(REFERENCE_PATH), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    orb = cv2.ORB_create(nfeatures=MAX_FEATURES)
    keypoints, descriptors = orb.detectAndCompute(image, None)
    if descriptors is None or len(keypoints) < MIN_MATCHES:
        return None
    return image, keypoints, descriptors


def _rotate_about_centre(quad: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate a quad about its own centre. Used to cancel the reference's built-in tilt."""
    if not degrees:
        return quad
    theta = np.radians(degrees)
    cos, sin = np.cos(theta), np.sin(theta)
    rotation = np.float32([[cos, -sin], [sin, cos]])
    centre = quad.mean(axis=0)
    return (centre + (quad - centre) @ rotation.T).astype(np.float32)


def register_back(image: np.ndarray) -> Registration | None:
    """Find the card back in `image`, or return None.

    Returns corners in the ORIGINAL image's coordinates, ordered to match the reference — so
    the first corner is the card's true top-left, whatever way up it was photographed.
    """
    reference = _reference()
    if reference is None or image is None or image.size == 0:
        return None
    ref_image, ref_keypoints, ref_descriptors = reference

    height, width = image.shape[:2]
    scale = min(1.0, WORK_MAX_DIM / max(height, width))
    work = (
        cv2.resize(image, (round(width * scale), round(height * scale)), cv2.INTER_AREA)
        if scale < 1.0
        else image
    )
    grey = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY) if work.ndim == 3 else work

    orb = cv2.ORB_create(nfeatures=MAX_FEATURES)
    keypoints, descriptors = orb.detectAndCompute(grey, None)
    if descriptors is None or len(keypoints) < MIN_MATCHES:
        return None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    try:
        pairs = matcher.knnMatch(ref_descriptors, descriptors, k=2)
    except cv2.error:  # pragma: no cover
        return None

    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < RATIO * n.distance]
    if len(good) < MIN_MATCHES:
        return None

    source = np.float32([ref_keypoints[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    target = np.float32([keypoints[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    homography, mask = cv2.findHomography(source, target, cv2.RANSAC, 4.0, maxIters=4000)
    if homography is None or mask is None:
        return None

    inliers = int(mask.sum())
    ratio = inliers / len(good)
    if inliers < MIN_INLIERS or ratio < MIN_INLIER_RATIO:
        return None

    ref_h, ref_w = ref_image.shape[:2]
    ref_corners = np.float32(
        [[0, 0], [ref_w - 1, 0], [ref_w - 1, ref_h - 1], [0, ref_h - 1]]
    )
    ref_corners = _rotate_about_centre(ref_corners, REFERENCE_ROTATION_DEG)
    ref_corners = ref_corners.reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(ref_corners, homography).reshape(4, 2) / scale
    centre = projected.mean(axis=0)
    projected = centre + (projected - centre) * REFERENCE_EXPANSION

    if not _plausible(projected, height, width):
        return None

    # Rotation the homography applied, read off its top-left 2x2 block.
    rotation = float(np.degrees(np.arctan2(homography[1, 0], homography[0, 0])))

    return Registration(
        corners=projected.astype(np.float32),
        inliers=inliers,
        matches=len(good),
        inlier_ratio=ratio,
        rotation_hint=rotation,
    )


def _plausible(quad: np.ndarray, height: int, width: int) -> bool:
    """Reject a homography that produced something that is not a card-shaped quad.

    RANSAC on repeated motifs can converge on a degenerate transform, and a collapsed or
    inside-out quad would rectify to noise.
    """
    from app.imaging.geometry import CARD_ASPECT, is_convex, quad_area, quad_aspect

    if not np.isfinite(quad).all():
        return False
    try:
        from app.imaging.geometry import order_corners

        ordered = order_corners(quad)
    except ValueError:
        return False
    if not is_convex(ordered):
        return False

    fraction = quad_area(ordered) / float(height * width)
    if not MIN_AREA_FRACTION <= fraction <= MAX_AREA_FRACTION:
        return False

    aspect = quad_aspect(ordered)
    error = min(abs(aspect - CARD_ASPECT), abs(aspect - 1.0 / CARD_ASPECT))
    return error <= ASPECT_TOLERANCE
