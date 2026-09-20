"""Locate a card's true border by fitting lines to its intensity edges.

The corners a contour gives you are wrong in three ways at once. Thresholding biases the
boundary outward. A real card has **rounded corners**, so the contour's extreme point is not
where the card's straight edges would meet. And anything crossing the edge — a finger, a
shadow, a sleeve seam — drags the trace with it.

Fitting solves all three. Each edge is sampled at many points; at each sample the exact
sub-pixel position of the intensity step is found by looking along the edge normal. A line is
then fitted through those points with outlier rejection, so a finger over 15% of one edge is
discarded rather than followed. Intersecting adjacent lines recovers the corner of the card's
*virtual* sharp rectangle — which is precisely the corner rectification needs, and which no
corner detector can see because the card does not physically have one.
"""

from dataclasses import dataclass

import cv2
import numpy as np

# Samples taken along each edge. More is steadier; the cost is trivial at this scale.
SAMPLES_PER_EDGE = 48

# Fraction of each edge skipped at both ends. Card corners are rounded, so the last few
# percent of an edge curves away and would bend the fit.
END_SKIP = 0.12

# How far to look either side of the rough edge, in pixels, for the real intensity step.
SEARCH_RADIUS = 14

# RANSAC, not sigma-clipping. A finger crossing an edge does not scatter samples randomly —
# it puts a *consistent* run of them on a different straight line, which iterative clipping
# happily fits halfway between. RANSAC picks the line with the largest consensus instead, so
# the card edge wins as long as it owns more of the edge than the obstruction does.
RANSAC_ITERATIONS = 120
RANSAC_INLIER_PX = 1.2
RANSAC_SEED = 12345

# What makes a fit trustworthy is how tightly the surviving samples lie on the line, not how
# many were discarded. An edge with fingers across two thirds of it still yields an exact line
# from the third that is visible — so the ratio only has to rule out fitting noise, while the
# absolute count and the residual do the real work.
MIN_INLIER_RATIO = 0.28
MIN_INLIERS = 12
MAX_RESIDUAL_PX = 1.2


@dataclass(frozen=True)
class EdgeFit:
    """One fitted card edge: a point, a unit direction, and how well it fitted."""

    point: np.ndarray
    direction: np.ndarray
    inlier_ratio: float
    residual_px: float
    inliers: int = 0

    @property
    def trustworthy(self) -> bool:
        return (
            self.inliers >= MIN_INLIERS
            and self.inlier_ratio >= MIN_INLIER_RATIO
            and self.residual_px <= MAX_RESIDUAL_PX
        )


def _subpixel_edge(profile: np.ndarray) -> float | None:
    """Offset of the strongest intensity step in a 1D profile, to sub-pixel precision.

    The step is found as the peak of the absolute first derivative, then refined by fitting a
    parabola to that peak and its neighbours — the standard trick, and worth roughly a tenth
    of a pixel over taking the peak sample directly.
    """
    if len(profile) < 5:
        return None

    gradient = np.abs(np.diff(profile.astype(np.float64)))
    peak = int(np.argmax(gradient))
    if gradient[peak] <= 0:
        return None
    # Ignore a peak at the very end of the search window: the true edge is probably outside it.
    if peak == 0 or peak >= len(gradient) - 1:
        return None

    left, centre, right = gradient[peak - 1], gradient[peak], gradient[peak + 1]
    denominator = left - 2 * centre + right
    shift = 0.0 if abs(denominator) < 1e-9 else 0.5 * (left - right) / denominator
    # The gradient sits between samples peak and peak+1, hence the half.
    return peak + 0.5 + float(np.clip(shift, -1.0, 1.0))


def _fit_line(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Total-least-squares line through points. Returns (centroid, unit direction, rms)."""
    centroid = points.mean(axis=0)
    centred = points - centroid
    # The principal direction is the singular vector with the largest singular value.
    _, _, vh = np.linalg.svd(centred, full_matrices=False)
    direction = vh[0] / np.linalg.norm(vh[0])
    normal = np.array([-direction[1], direction[0]])
    residuals = centred @ normal
    return centroid, direction, float(np.sqrt(np.mean(residuals**2)))


def fit_edge(
    grey: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    search_radius: int = SEARCH_RADIUS,
) -> EdgeFit | None:
    """Fit a line to the real intensity edge running roughly from `start` to `end`."""
    start = np.asarray(start, dtype=np.float64)
    end = np.asarray(end, dtype=np.float64)
    span = end - start
    length = float(np.linalg.norm(span))
    if length < 8:
        return None

    along = span / length
    normal = np.array([-along[1], along[0]])
    height, width = grey.shape[:2]

    found: list[np.ndarray] = []
    for t in np.linspace(END_SKIP, 1.0 - END_SKIP, SAMPLES_PER_EDGE):
        base = start + along * (t * length)

        if not (0 <= base[0] < width and 0 <= base[1] < height):
            continue

        offsets = np.arange(-search_radius, search_radius + 1, dtype=np.float64)
        samples = base[None, :] + normal[None, :] * offsets[:, None]
        xs, ys = samples[:, 0], samples[:, 1]

        # The search window is deliberately NOT rejected for running past the frame. A card
        # that fills the frame — which is what produces the best captures, and what the
        # capture guide asks for — has its edges within a search radius of the border, so
        # skipping those samples abandons the fit on exactly the images it helps most.
        # BORDER_REPLICATE pads with a constant, which has zero gradient and so cannot
        # invent an edge; it just contributes nothing.

        # Bilinear sampling along the normal, so the profile is not quantised to whole pixels.
        profile = cv2.remap(
            grey,
            xs.astype(np.float32).reshape(-1, 1),
            ys.astype(np.float32).reshape(-1, 1),
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        ).ravel()

        offset = _subpixel_edge(profile)
        if offset is None:
            continue
        found.append(base + normal * (offset - search_radius))

    if len(found) < 8:
        return None

    points = np.array(found)
    inliers = _ransac_inliers(points)
    if inliers is None or inliers.sum() < 8:
        return None

    centroid, direction, rms = _fit_line(points[inliers])
    return EdgeFit(
        point=centroid,
        direction=direction,
        inlier_ratio=float(inliers.sum()) / len(points),
        residual_px=rms,
        inliers=int(inliers.sum()),
    )


def _ransac_inliers(points: np.ndarray) -> np.ndarray | None:
    """Largest set of samples consistent with a single straight line."""
    count = len(points)
    if count < 8:
        return None

    rng = np.random.default_rng(RANSAC_SEED)
    best: np.ndarray | None = None
    best_count = 0

    for _ in range(RANSAC_ITERATIONS):
        i, j = rng.choice(count, size=2, replace=False)
        span = points[j] - points[i]
        length = float(np.linalg.norm(span))
        if length < 1e-6:
            continue
        normal = np.array([-span[1], span[0]]) / length
        distances = np.abs((points - points[i]) @ normal)
        consensus = distances <= RANSAC_INLIER_PX
        hits = int(consensus.sum())
        if hits > best_count:
            best_count, best = hits, consensus

    if best is None:
        return None

    # One refinement pass: refit on the consensus set and re-select, which recovers samples the
    # two-point seed happened to just miss.
    centroid, direction, _ = _fit_line(points[best])
    normal = np.array([-direction[1], direction[0]])
    return np.abs((points - centroid) @ normal) <= RANSAC_INLIER_PX


def _intersect(a: EdgeFit, b: EdgeFit) -> np.ndarray | None:
    """Intersection of two fitted lines, or None if they are near-parallel."""
    matrix = np.array([a.direction, -b.direction]).T
    determinant = np.linalg.det(matrix)
    if abs(determinant) < 1e-6:
        return None
    t = np.linalg.solve(matrix, b.point - a.point)
    return a.point + a.direction * t[0]


def refine_quad(
    grey: np.ndarray, quad: np.ndarray, search_radius: int = SEARCH_RADIUS
) -> tuple[np.ndarray, dict] | None:
    """Refine a rough quad to the card's true edges.

    Returns the refined corners and a diagnostics dict, or None when the edges could not be
    fitted confidently — in which case the caller keeps the corners it already had rather than
    trusting a worse answer.
    """
    corners = np.asarray(quad, dtype=np.float64).reshape(4, 2)

    fits: list[EdgeFit] = []
    for index in range(4):
        fit = fit_edge(grey, corners[index], corners[(index + 1) % 4], search_radius)
        if fit is None or not fit.trustworthy:
            return None
        fits.append(fit)

    refined = []
    for index in range(4):
        # Corner i is where edge (i-1) meets edge i.
        point = _intersect(fits[index - 1], fits[index])
        if point is None:
            return None
        refined.append(point)

    result = np.array(refined, dtype=np.float32)
    shift = float(np.max(np.linalg.norm(result - corners, axis=1)))

    # Sanity-check the *shape*, not the distance moved. A large shift is expected and welcome
    # when the starting quad was poor — that is the whole point of refining. What would signal
    # a lock onto something else is the result no longer being a card: a collapsed area or an
    # aspect ratio that has swung away from the rough quad's.
    from app.imaging.geometry import quad_area, quad_aspect

    before, after = quad_area(corners), quad_area(result)
    if after <= 0 or not 0.7 <= after / before <= 1.4:
        return None

    aspect_before, aspect_after = quad_aspect(corners), quad_aspect(result)
    if aspect_before <= 0 or not 0.8 <= aspect_after / aspect_before <= 1.25:
        return None

    return result, {
        "max_shift_px": round(shift, 2),
        "residual_px": round(max(f.residual_px for f in fits), 2),
        "inlier_ratio": round(min(f.inlier_ratio for f in fits), 3),
        "min_inliers": min(f.inliers for f in fits),
    }
