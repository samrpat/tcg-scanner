"""Find the card in a photograph.

Classical rather than learned: a quadrilateral on a contrasting background is a solved problem,
and this route ships no model, needs no accelerator, and runs in milliseconds on a Pi.

The important lesson from real photographs is that **no single edge strategy works everywhere**.
Canny with Otsu thresholds is excellent on a card lying on a plain mat and finds nothing at all
when the card is held up against a face and a textured wall — the scene's dominant contrast is
elsewhere, so the card's own outline never survives thresholding. Adaptive thresholding handles
that case and is in turn noisy on clean scenes. So every strategy runs, all their candidates go
into one pool, and the best-scoring quad wins.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

import cv2
import numpy as np

from app.imaging.edges import refine_quad
from app.imaging.geometry import (
    CARD_ASPECT,
    is_convex,
    order_corners,
    quad_area,
    quad_aspect,
)
from app.imaging.verify import border_evidence

# Detection runs on a downscaled copy; corners scale back afterwards. A card outline is a
# large-scale feature, so working small costs nothing in accuracy and a lot less in time.
WORK_MAX_DIM = 1000

# A card should occupy a real portion of the frame but not all of it. The upper bound matters:
# adaptive thresholding readily returns the image border itself as a perfect quad.
MIN_AREA_FRACTION = 0.03
MAX_AREA_FRACTION = 0.92

# How far the quad's aspect may stray from 88:63 before it stops looking like a card.
# Generous, because perspective skews aspect well before it stops being recognisable.
ASPECT_TOLERANCE = 0.38

# Two independent shape checks, because either alone is fooled.
#
# `fill` is contour area over quad area: a real card fills its own quad, a hand does not. But a
# finger crossing the card edge dents the contour and drags fill down on a perfectly good card.
#
# `convexity` is contour area over convex-hull area: a card is convex, so a card with a finger
# over one edge still scores high, while a whole hand — fingers splayed, deep concavities —
# scores low. Requiring a candidate to pass fill OR (convexity AND a slightly relaxed fill)
# admits the occluded card and still rejects the hand.
MIN_FILL = 0.72
MIN_CONVEXITY = 0.88
RELAXED_FILL = 0.55

# Candidates that fail the shape gate are kept but marked, rather than discarded. The gate
# measures how well a quad is filled by the contour it came from, which is the wrong question
# for a hull candidate whose contour traced only part of the card's border — and that is
# exactly how a Pokémon card back behaves against a dark mat. Discarding them threw away the
# correct answer at 0.88 confidence and left the pokéball at 0.29 to win by default.
WEAK_SHAPE_PENALTY = 0.75

# How many candidates get the expensive edge-support check. Detection is ~30ms; each check is
# a few more, and being right here decides every measurement downstream.
VERIFY_TOP_N = 6

# Edge support blends into the final score. A quad whose four sides sit on real, straight
# intensity edges is a card boundary; one drawn around a circular graphic is not, and no
# amount of aspect-and-area reasoning can tell them apart.
SUPPORT_FLOOR = 0.55

# Border evidence — is there card on one side of this quad and background on the other — is
# useless as an absolute score, because a dark blue card back on a black mat has genuinely low
# contrast whatever the crop. Comparing *candidates within one image* controls for both the
# card's colour and the mat, and then it is the sharpest signal available: only the true outer
# boundary has background outside it. An artwork panel or a pokéball has more card outside it.
BORDER_WEIGHT = 0.45

CONTOUR_POOL = 40

# Fine to coarse, and every 4-vertex result is kept rather than stopping at the first.
# A coarse-only sweep was the reason a clean card back found nothing: its contour collapsed
# to two vertices at 0.02 and every larger epsilon only made that worse.
APPROX_EPSILONS = (0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.06)

# Two quads overlapping by more than this are competing descriptions of the same thing, and
# only the better-scoring one survives.
OVERLAP_THRESHOLD = 0.70

# A Pokémon card is a set of concentric rectangles: the card edge, a border frame just inside
# it, then the artwork window. The inner frames are portrait and very nearly card-shaped, so
# they score almost as well as the card itself and can win — cropping the listing photo inside
# the card's own border and, worse, cutting away the edge where wear is measured.
#
# Concentric frames of one card sit within a few percent of each other. Anything much larger is
# a different object entirely — the mat the card is lying on — which is why this is bounded at
# both ends rather than being a plain "prefer the bigger one".
NESTED_FRAME_MAX_RATIO = 1.45
NESTED_ASPECT_TOLERANCE = 0.10

# The outer frame of a card scores about as well as its inner frame — they are the same object
# seen twice. A candidate that scores *materially* worse is not the same card's outer border,
# it is a different object that merely overlaps: a mat, a shadow, a table edge. Requiring the
# replacement to be nearly as good is what keeps "prefer the outer one" from quietly becoming
# "prefer the bigger one", which is the bug this rule was written to fix in the first place.
NESTED_MIN_SCORE_RATIO = 0.85

# A card photographed sideways is perfectly possible, just uncommon. A card's artwork window is
# *always* landscape, and its aspect (0.65-0.75) sits right on top of a sideways card's 0.716 —
# so on the numbers alone the two are indistinguishable, and the artwork usually has the cleaner
# outline of the pair. This came from a real capture that rectified to the card art.
#
# Landscape therefore has to win clearly rather than narrowly. A genuinely sideways card still
# wins when it is the only candidate, and a wrong call costs one drag of the corner tool; a
# listing photo of the artwork alone costs a sale.
# 0.70 was tried and rejected: it broke detection of a genuinely sideways card, which is a
# worse trade than it looks. The artwork-only case is already flagged for review and costs one
# drag of the corner tool, whereas failing to find a sideways card costs the whole capture.
LANDSCAPE_PENALTY = 0.82

# Fraction of the frame's shorter side a quad must stay clear of the border by. A quad hugging
# the frame is usually the background — a mat, a table, the image edge itself — whose aspect is
# the frame's own and therefore often close to a sideways card's.
FRAME_MARGIN_FRACTION = 0.01
FRAME_HUGGING_PENALTY = 0.75


@dataclass(frozen=True)
class Detection:
    """A located card. `corners` are in the ORIGINAL image's coordinates, TL/TR/BR/BL."""

    corners: np.ndarray
    confidence: float
    method: str
    area_fraction: float
    aspect: float
    rectangularity: float = 1.0
    # The traced outline the quad was fitted to, in original-image coordinates. A phone lens
    # bows a straight card edge by several pixels, and a 4-point homography cannot represent
    # that — it can only pass a straight line through the corners and let the middle drift.
    # Keeping the real boundary lets the warp follow the card's actual edge.
    contour: np.ndarray | None = None
    # Diagnostics from line fitting when it was used: how far corners moved, fit residual,
    # and what fraction of samples survived outlier rejection.
    edge_fit: dict | None = None

    def scaled(self, factor: float) -> "Detection":
        """This detection expressed in a coordinate space `factor` times larger.

        Used to lift corners found on a half-size decode back into the original file's
        coordinates. Area fraction and aspect are ratios and so are unaffected; only the point
        geometry moves.
        """
        if factor == 1.0:
            return self
        return replace(
            self,
            corners=(self.corners * factor).astype(np.float32),
            contour=(
                None if self.contour is None else (self.contour * factor).astype(np.float32)
            ),
        )

    def as_list(self) -> list[list[float]]:
        return [[round(float(x), 2), round(float(y), 2)] for x, y in self.corners]


def _work_image(image: np.ndarray) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    scale = min(1.0, WORK_MAX_DIM / max(height, width))
    if scale >= 1.0:
        return image, 1.0
    resized = cv2.resize(
        image, (int(round(width * scale)), int(round(height * scale))), interpolation=cv2.INTER_AREA
    )
    return resized, scale


# --- Edge strategies ---------------------------------------------------------------------
# Each returns a binary mask for findContours. Cheap enough to run all of them.

def _canny_edges(grey: np.ndarray) -> np.ndarray:
    """Otsu-thresholded Canny. Best on a card against a plain, evenly lit background."""
    smoothed = cv2.bilateralFilter(grey, 9, 75, 75)
    otsu, _ = cv2.threshold(smoothed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    edges = cv2.Canny(smoothed, max(10.0, otsu * 0.5), otsu)
    return cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)


def _adaptive_edges(grey: np.ndarray) -> np.ndarray:
    """Local thresholding. Handles uneven lighting and busy backgrounds, where a global
    threshold throws the card's own outline away."""
    smoothed = cv2.GaussianBlur(grey, (5, 5), 0)
    mask = cv2.adaptiveThreshold(
        smoothed, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 7
    )
    # Closing bridges gaps so the outline forms a loop. It also biases the boundary outward
    # by a few pixels, which `_refine_corners` takes back out afterwards.
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)


def _gradient_edges(grey: np.ndarray) -> np.ndarray:
    """Morphological gradient. Picks up low-contrast boundaries that Canny drops."""
    smoothed = cv2.GaussianBlur(grey, (5, 5), 0)
    gradient = cv2.morphologyEx(smoothed, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    _, mask = cv2.threshold(gradient, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)


def _clahe_edges(grey: np.ndarray) -> np.ndarray:
    """Canny after local contrast equalisation.

    A dark card border on a dark mat — a blue Pokémon back on a black play mat, say — has
    almost no luminance step at the boundary, and Otsu's global threshold discards it in favour
    of whatever is brighter elsewhere in the frame. CLAHE lifts contrast tile by tile, which
    brings that boundary back without blowing out the rest of the image.
    """
    equalised = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(grey)
    smoothed = cv2.bilateralFilter(equalised, 9, 75, 75)
    otsu, _ = cv2.threshold(smoothed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    edges = cv2.Canny(smoothed, max(10.0, otsu * 0.5), otsu)
    return cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)


def _saturation_edges(colour: np.ndarray) -> np.ndarray:
    """Gradient over saturation. A printed card is usually more saturated than skin or a wall,
    so this separates them where luminance alone does not."""
    if colour.ndim != 3:
        return _gradient_edges(colour)
    saturation = cv2.cvtColor(colour, cv2.COLOR_BGR2HSV)[:, :, 1]
    return _gradient_edges(saturation)


STRATEGIES: tuple[tuple[str, Callable[[np.ndarray, np.ndarray], np.ndarray]], ...] = (
    ("canny", lambda grey, colour: _canny_edges(grey)),
    ("adaptive", lambda grey, colour: _adaptive_edges(grey)),
    ("gradient", lambda grey, colour: _gradient_edges(grey)),
    ("clahe", lambda grey, colour: _clahe_edges(grey)),
    ("saturation", lambda grey, colour: _saturation_edges(colour)),
)


# --- Scoring -----------------------------------------------------------------------------

# How much of the frame a correctly located card occupies. Learned from the real captures:
# every correct detection landed between 0.25 and 0.62, every mis-detection was either a
# graphic *inside* the card (a pokéball at 0.15) or the mat *around* it (0.77-0.81).
#
# The old score rose monotonically with size and then plateaued, which gave a respectable mark
# to both failure modes. A band gives them nothing while leaving the correct range untouched.
SIZE_IDEAL = (0.30, 0.66)
SIZE_ZERO_ABOVE = 0.88
SIZE_FALLOFF = 2.5


def _size_score(area_fraction: float) -> float:
    low, high = SIZE_IDEAL
    if low <= area_fraction <= high:
        return 1.0
    if area_fraction < low:
        # Falls away steeply: a quad at a third of the ideal size is far more likely to be a
        # graphic on the card than a distant card.
        return max(0.0, (area_fraction / low) ** SIZE_FALLOFF)
    return max(0.0, 1.0 - (area_fraction - high) / (SIZE_ZERO_ABOVE - high))


def _aspect_score(aspect: float) -> float:
    """1.0 at a perfect 88:63, falling off linearly, 0 outside tolerance.

    Compared against both the portrait and landscape aspect, because a card photographed
    sideways is still a card.
    """
    if aspect <= 0:
        return 0.0
    best = min(abs(aspect - CARD_ASPECT), abs(aspect - 1.0 / CARD_ASPECT))
    return max(0.0, 1.0 - best / ASPECT_TOLERANCE)


def _score(
    quad: np.ndarray,
    contour_area: float,
    frame_area: float,
    method: str,
    frame_shape: tuple[int, int] | None = None,
) -> tuple[float, float, float, float]:
    area = quad_area(quad)
    area_fraction = area / frame_area if frame_area else 0.0
    aspect = quad_aspect(quad)
    rectangularity = min(1.0, contour_area / area) if area > 0 else 0.0

    aspect_score = _aspect_score(aspect)
    size_score = _size_score(area_fraction)

    penalty = 0.6 if method.endswith("min_area_rect") else 1.0
    if aspect < 1.0:
        penalty *= LANDSCAPE_PENALTY

    if frame_shape is not None:
        height, width = frame_shape
        margin = min(
            float(quad[:, 0].min()),
            float(quad[:, 1].min()),
            width - float(quad[:, 0].max()),
            height - float(quad[:, 1].max()),
        )
        if margin < FRAME_MARGIN_FRACTION * min(height, width):
            penalty *= FRAME_HUGGING_PENALTY

    confidence = (
        0.55 * aspect_score + 0.25 * size_score + 0.20 * rectangularity
    ) * penalty
    return confidence, area_fraction, aspect, rectangularity


def _candidates_from(mask: np.ndarray, label: str, frame_area: float):
    """Every plausible quad in one edge mask.

    RETR_LIST rather than RETR_EXTERNAL: a held card sits inside the outline of the hand and
    the body, so restricting to outermost contours loses it entirely.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:CONTOUR_POOL]:
        contour_area = cv2.contourArea(contour)
        if not (MIN_AREA_FRACTION * frame_area <= contour_area <= MAX_AREA_FRACTION * frame_area):
            continue

        found: list[tuple[np.ndarray, str]] = []
        seen: set[tuple] = set()

        # The raw contour and its convex hull are both approximated. On a real photograph the
        # raw contour of a held card is ragged — rounded corners, fingers crossing the edge,
        # sleeve glare — and rarely simplifies to exactly four vertices at any epsilon. Its
        # convex hull does, reliably, because a card is convex and the noise is all inward.
        hull = cv2.convexHull(contour)
        hull_area = cv2.contourArea(hull)
        convexity = min(1.0, contour_area / hull_area) if hull_area > 0 else 0.0

        # Both the raw contour and its convex hull are approximated. On a real photograph the
        # raw contour of a held card is ragged — rounded corners, fingers crossing the edge —
        # and rarely simplifies to exactly four vertices at any epsilon. Its hull does.
        for source, tag in ((contour, label), (hull, f"{label}:hull")):
            perimeter = cv2.arcLength(source, True)
            if perimeter <= 0:
                continue
            for epsilon in APPROX_EPSILONS:
                approx = cv2.approxPolyDP(source, epsilon * perimeter, True)
                if len(approx) != 4:
                    continue
                points = approx.reshape(4, 2).astype(np.float32)
                key = tuple(np.round(points.ravel(), 1))
                if key in seen:
                    continue
                seen.add(key)
                found.append((points, tag))

        found.append(
            (cv2.boxPoints(cv2.minAreaRect(contour)).astype(np.float32), f"{label}:min_area_rect")
        )

        for points, method in found:
            try:
                ordered = order_corners(points)
            except ValueError:
                continue
            if not is_convex(ordered):
                continue

            area_fraction = quad_area(ordered) / frame_area if frame_area else 0.0
            if not (MIN_AREA_FRACTION <= area_fraction <= MAX_AREA_FRACTION):
                continue

            outline = contour.reshape(-1, 2).astype(np.float32)
            yield ordered, contour_area, convexity, method, outline


def _search_radius(scale: float) -> int:
    """How far to hunt for the true edge, in full-resolution pixels.

    The rough quad comes from a downscaled copy *and* from a thresholded region whose boundary
    is biased outward, so its error is several working pixels — which is several times
    `1/scale` at full resolution. Search too narrowly and the true edge falls outside the
    window, every sample fails, and the fit is abandoned on exactly the high-resolution
    captures it would help most.
    """
    from app.imaging.edges import SEARCH_RADIUS

    return int(max(SEARCH_RADIUS, round(8.0 / max(scale, 1e-6))))


def _refine_corners(grey: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Pull each corner onto the real intensity edge, to sub-pixel accuracy.

    Thresholding and morphological closing both bias a boundary outward by a few pixels. That
    sounds harmless until you remember the rectified image is 20 px per millimetre: a 6 px bias
    is 0.3 mm of card that is not there, sitting exactly where edgewear is measured. Corner
    refinement costs microseconds and takes it back.
    """
    points = np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2).copy()
    height, width = grey.shape[:2]
    # Window must not exceed the frame, and a large window on a small image walks the corner
    # onto unrelated structure.
    half = max(3, min(11, min(height, width) // 40))
    try:
        cv2.cornerSubPix(
            grey,
            points,
            (half, half),
            (-1, -1),
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.02),
        )
    except cv2.error:  # pragma: no cover - refinement is an improvement, never a requirement
        return np.asarray(corners, dtype=np.float32).reshape(4, 2)

    refined = points.reshape(4, 2)
    # Refinement can bolt to a nearby feature. Reject a corner that moved implausibly far.
    original = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    moved = np.linalg.norm(refined - original, axis=1)
    limit = half * 1.5
    return np.where((moved > limit)[:, None], original, refined).astype(np.float32)


def _contained_fraction(inner: np.ndarray, outer: np.ndarray) -> float:
    """How much of `inner`'s area falls inside `outer`, 0..1."""
    inner_area = quad_area(inner)
    if inner_area <= 0:
        return 0.0
    intersection, _ = cv2.intersectConvexConvex(
        np.asarray(inner, dtype=np.float32), np.asarray(outer, dtype=np.float32)
    )
    return float(min(1.0, intersection / inner_area))


def _suppress_overlaps(candidates: list[Detection]) -> list[Detection]:
    """Keep the best-scoring quad and drop anything describing the same region.

    Suppression used to prefer the *larger* quad, on the theory that a card contains its own
    artwork window. That is true, but a card is also contained by the mat it is lying on, and
    "bigger wins" then throws away a perfect detection of the card in favour of the
    background. Scoring decides instead — which is what the score is for — and the artwork case
    is handled where it belongs, by penalising the landscape orientation an artwork window
    always has.
    """
    kept: list[Detection] = []
    for candidate in sorted(candidates, key=lambda c: c.confidence, reverse=True):
        replaced = False
        overlaps = False

        for index, better in enumerate(kept):
            inside = _contained_fraction(candidate.corners, better.corners)
            around = _contained_fraction(better.corners, candidate.corners)
            if max(inside, around) < OVERLAP_THRESHOLD:
                continue
            overlaps = True

            # Same card, outer frame versus inner frame: take the outer one even though it
            # scored slightly lower, because cropping inside the card's own border loses the
            # edge that wear is measured on.
            larger = quad_area(candidate.corners)
            smaller = quad_area(better.corners)
            if smaller <= 0:
                break
            ratio = larger / smaller
            similar_shape = (
                abs(_aspect_score(candidate.aspect) - _aspect_score(better.aspect))
                <= NESTED_ASPECT_TOLERANCE
            )
            # A minAreaRect is a bounding box, so it is *always* larger than the quad it came
            # from and *always* similarly shaped. Letting one qualify as "the outer frame of
            # the same card" replaces every good detection with a loose box around it.
            clean_quad = (
                not candidate.method.endswith("min_area_rect")
                and candidate.rectangularity >= 0.85
            )
            comparable = candidate.confidence >= better.confidence * NESTED_MIN_SCORE_RATIO
            if (
                clean_quad
                and comparable
                and 1.0 < ratio <= NESTED_FRAME_MAX_RATIO
                and similar_shape
            ):
                kept[index] = candidate
                replaced = True
            break

        if not overlaps:
            kept.append(candidate)
        elif replaced:
            continue

    return kept


def detect_card(image: np.ndarray) -> Detection | None:
    """Locate the card, or return None.

    Returning None is a real answer. A confident wrong quad silently mis-measures every defect
    on the card, so the pipeline would rather open a review.
    """
    if image is None or image.size == 0:
        return None

    work, scale = _work_image(image)
    if work.size == 0:
        return None

    grey = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY) if work.ndim == 3 else work
    frame_area = float(work.shape[0] * work.shape[1])

    candidates: list[Detection] = []

    for label, strategy in STRATEGIES:
        try:
            mask = strategy(grey, work)
        except cv2.error:  # pragma: no cover - a strategy failing must not sink the rest
            continue

        for ordered, contour_area, convexity, method, outline in _candidates_from(
            mask, label, frame_area
        ):
            confidence, area_fraction, aspect, rectangularity = _score(
                ordered, contour_area, frame_area, method, work.shape[:2]
            )
            passes_shape = rectangularity >= MIN_FILL or (
                convexity >= MIN_CONVEXITY and rectangularity >= RELAXED_FILL
            )
            if confidence <= 0:
                continue
            if not passes_shape:
                confidence = round(confidence * WEAK_SHAPE_PENALTY, 4)
            candidates.append(
                Detection(
                    corners=ordered,
                    confidence=round(confidence, 4),
                    method=method,
                    area_fraction=round(area_fraction, 4),
                    aspect=round(aspect, 4),
                    rectangularity=round(rectangularity, 4),
                    contour=outline,
                )
            )

    if not candidates:
        return None

    shortlist = sorted(_suppress_overlaps(candidates), key=lambda c: -c.confidence)[:VERIFY_TOP_N]

    # Search cheaply, verify expensively.
    #
    # Aspect, area and fill can all be satisfied by something that is not a card — a pokéball
    # on the card back has a card-shaped bounding box. What separates a real card boundary from
    # a graphic's bounding box is whether the four sides actually lie along straight intensity
    # edges in the image. That is measurable, so measure it rather than inferring it: fit all
    # four edges of each shortlisted candidate and let the evidence rank them.
    full_grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    radius = _search_radius(scale)

    verified: list[tuple[float, Detection, np.ndarray, dict]] = []
    border_scores: list[float] = []
    rows: list[tuple[Detection, np.ndarray, dict, float]] = []

    for candidate in shortlist:
        corners_full = candidate.corners / scale
        fitted = refine_quad(full_grey, corners_full, search_radius=radius)
        if fitted is None:
            refined, diagnostics, support = corners_full, {}, 0.0
        else:
            refined, diagnostics = fitted
            support = _support_score(diagnostics)
        border = border_evidence(image, refined).score
        border_scores.append(border)
        rows.append((candidate, refined, diagnostics, support))

    # Rank border evidence relative to the best candidate in *this* image, never on an
    # absolute scale.
    strongest = max(border_scores) if border_scores else 0.0
    for (candidate, refined, diagnostics, support), border in zip(rows, border_scores, strict=True):
        relative = border / strongest if strongest > 1e-6 else 0.0
        evidence = (1 - BORDER_WEIGHT) * support + BORDER_WEIGHT * relative
        score = candidate.confidence * (SUPPORT_FLOOR + (1 - SUPPORT_FLOOR) * evidence)
        if diagnostics:
            diagnostics = {**diagnostics, "border": round(border, 3)}
        verified.append((score, candidate, refined, diagnostics))

    score, best, refined_full, diagnostics = max(verified, key=lambda v: v[0])
    method = best.method + ("+lines" if diagnostics else "")
    if not diagnostics:
        refined_full = _refine_corners(full_grey, refined_full)
    else:
        diagnostics = {**diagnostics, "support": round(_support_score(diagnostics), 3)}

    # Edge support decides which candidate wins, but it is not reported as the confidence.
    # Multiplying it in would drag a correct, well-verified detection from 0.98 to 0.55 and
    # trip the review threshold on exactly the captures that worked. Rank on evidence, report
    # the shape score, and keep the support figure alongside it for diagnosis.
    return Detection(
        corners=refined_full,
        confidence=best.confidence,
        method=method,
        area_fraction=best.area_fraction,
        aspect=best.aspect,
        rectangularity=best.rectangularity,
        contour=best.contour / scale if best.contour is not None else None,
        edge_fit=diagnostics or None,
    )


def _support_score(diagnostics: dict) -> float:
    """How strongly the image agrees that these four lines are the card's border.

    Combines how tightly the sampled edge points fitted their line with how many of them
    survived outlier rejection. A quad around a circular graphic has neither.
    """
    from app.imaging.edges import MAX_RESIDUAL_PX, SAMPLES_PER_EDGE

    residual = float(diagnostics.get("residual_px", MAX_RESIDUAL_PX))
    inliers = float(diagnostics.get("min_inliers", 0))
    tightness = max(0.0, 1.0 - residual / MAX_RESIDUAL_PX)
    coverage = min(1.0, inliers / (SAMPLES_PER_EDGE * 0.5))
    return tightness * coverage
