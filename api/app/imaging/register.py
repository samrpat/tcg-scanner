"""Register a capture against a reference image, and recover the exact homography.

This is the technique that fixed card backs in Phase 2, generalised so recognition can use it
against any candidate's official art. It does three jobs at once, which is why it is worth
preferring over edge detection wherever a reference exists:

- **Verification.** The inlier count is the strongest identification signal available. A
  perceptual hash says "these look alike"; hundreds of features agreeing on one homography says
  "this is that card".
- **Cropping.** The homography maps the reference's corners onto the capture, giving a card
  boundary derived from hundreds of correspondences rather than four fitted edges.
- **Orientation.** The rotation falls out of the homography, which closes Phase 2's deferred
  upside-down problem without a separate heuristic.
"""

from dataclasses import dataclass

import cv2
import numpy as np

WORK_MAX_DIM = 1100

# Brute-force Hamming matching is quadratic in feature count. 1200 still yields hundreds of
# inliers on a real match, which is far more than a homography needs; 3000 took the test suite
# from 5s to 83s for no accuracy gain.
MAX_FEATURES = 1200

# Lowe's ratio. Card art contains repeated motifs — a back's logo appears twice, energy symbols
# repeat — so this has to be strict or the repeats manufacture confident nonsense.
RATIO = 0.72

MIN_MATCHES = 18
RANSAC_REPROJECTION_PX = 4.0


@dataclass(frozen=True)
class Match:
    """A registration result. `corners` are the reference's corners in capture coordinates."""

    corners: np.ndarray
    inliers: int
    matches: int
    inlier_ratio: float
    rotation_deg: float
    homography: np.ndarray
    # The surviving correspondences, in reference and capture coordinates. Kept because a
    # homography throws away most of what registration learned: it collapses hundreds of
    # agreeing point pairs into the one planar, pinhole-camera transform that best fits them.
    # A card is not perfectly flat and a lens is not a pinhole, so the residual is real, and
    # the points still carry it.
    reference_points: np.ndarray | None = None
    capture_points: np.ndarray | None = None

    def as_dict(self) -> dict:
        return {
            "inliers": self.inliers,
            "matches": self.matches,
            "inlier_ratio": round(self.inlier_ratio, 3),
            "rotation_deg": round(self.rotation_deg, 1),
        }


def _features(image: np.ndarray, detector) -> tuple:
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return detector.detectAndCompute(grey, None)


def _downscale(image: np.ndarray) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    scale = min(1.0, WORK_MAX_DIM / max(height, width))
    if scale >= 1.0:
        return image, 1.0
    return cv2.resize(image, (round(width * scale), round(height * scale)), cv2.INTER_AREA), scale


def register(
    capture: np.ndarray,
    reference: np.ndarray,
    *,
    min_inliers: int = 14,
    min_inlier_ratio: float = 0.32,
) -> Match | None:
    """Find `reference` inside `capture`.

    Returns None rather than a weak answer: a wrong homography rectifies a card into noise and
    would attach a wrong identity, which is worse than admitting ignorance.
    """
    if capture is None or reference is None or capture.size == 0 or reference.size == 0:
        return None

    work, scale = _downscale(capture)
    detector = cv2.ORB_create(nfeatures=MAX_FEATURES)

    ref_keypoints, ref_descriptors = _features(reference, detector)
    keypoints, descriptors = _features(work, detector)
    if (
        ref_descriptors is None
        or descriptors is None
        or len(ref_keypoints) < MIN_MATCHES
        or len(keypoints) < MIN_MATCHES
    ):
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

    homography, mask = cv2.findHomography(
        source, target, cv2.RANSAC, RANSAC_REPROJECTION_PX, maxIters=4000
    )
    if homography is None or mask is None:
        return None

    inliers = int(mask.sum())
    ratio = inliers / len(good)
    if inliers < min_inliers or ratio < min_inlier_ratio:
        return None

    ref_h, ref_w = reference.shape[:2]
    corners = cv2.perspectiveTransform(
        np.float32([[0, 0], [ref_w - 1, 0], [ref_w - 1, ref_h - 1], [0, ref_h - 1]]).reshape(
            -1, 1, 2
        ),
        homography,
    ).reshape(4, 2) / scale

    if not np.isfinite(corners).all():
        return None

    rotation = float(np.degrees(np.arctan2(homography[1, 0], homography[0, 0])))

    keep = mask.ravel().astype(bool)
    return Match(
        corners=corners.astype(np.float32),
        inliers=inliers,
        matches=len(good),
        inlier_ratio=ratio,
        rotation_deg=rotation,
        homography=homography,
        reference_points=source.reshape(-1, 2)[keep],
        capture_points=(target.reshape(-1, 2)[keep]) / scale,
    )
