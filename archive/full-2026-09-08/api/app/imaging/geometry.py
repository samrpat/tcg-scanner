"""Card geometry and corner ordering.

The physical constants here are the same ones the grading rubric is written against
(docs/CONDITION.md). One definition means a change to card size cannot desynchronise the
dewarp from the measurement thresholds.
"""

import numpy as np

from app.conditioning.rubric import CARD_LENGTH_MM
from app.conditioning.rubric import CARD_WIDTH_MM as _RUBRIC_WIDTH_MM

# Portrait orientation: 63mm across, 88mm tall.
CARD_WIDTH_MM: float = _RUBRIC_WIDTH_MM   # 63.0
CARD_HEIGHT_MM: float = CARD_LENGTH_MM    # 88.0
CARD_ASPECT: float = CARD_HEIGHT_MM / CARD_WIDTH_MM  # ~1.3968


def output_size(px_per_mm: float) -> tuple[int, int]:
    """(width, height) of the rectified image at a given scale."""
    if px_per_mm <= 0:
        raise ValueError("px_per_mm must be positive")
    return (round(CARD_WIDTH_MM * px_per_mm), round(CARD_HEIGHT_MM * px_per_mm))


def order_corners(points: np.ndarray) -> np.ndarray:
    """Order four points as top-left, top-right, bottom-right, bottom-left.

    Uses coordinate sums and differences rather than angles: the top-left has the smallest
    x+y and the top-right the smallest y-x. Stable for the mildly rotated, mildly
    perspective-distorted quads a hand-held photo produces, and unlike an angular sort it
    does not depend on a reliable centroid.
    """
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)

    total = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()  # y - x

    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(total)]  # top-left
    ordered[2] = pts[np.argmax(total)]  # bottom-right
    ordered[1] = pts[np.argmin(diff)]   # top-right
    ordered[3] = pts[np.argmax(diff)]   # bottom-left

    if len({tuple(p) for p in ordered}) != 4:
        raise ValueError("degenerate quad: corners are not distinct")
    return ordered


def quad_area(points: np.ndarray) -> float:
    """Shoelace area of a quad."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    x, y = pts[:, 0], pts[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def side_lengths(ordered: np.ndarray) -> tuple[float, float, float, float]:
    """Edge lengths of an ordered quad: top, right, bottom, left."""
    p = np.asarray(ordered, dtype=np.float64).reshape(4, 2)
    return (
        float(np.linalg.norm(p[1] - p[0])),
        float(np.linalg.norm(p[2] - p[1])),
        float(np.linalg.norm(p[3] - p[2])),
        float(np.linalg.norm(p[0] - p[3])),
    )


def quad_aspect(ordered: np.ndarray) -> float:
    """Height / width of an ordered quad, using averaged opposite sides."""
    top, right, bottom, left = side_lengths(ordered)
    width = (top + bottom) / 2
    height = (left + right) / 2
    if width <= 0:
        return 0.0
    return height / width


def is_convex(ordered: np.ndarray) -> bool:
    """True when the cross products of consecutive edges all share a sign."""
    p = np.asarray(ordered, dtype=np.float64).reshape(4, 2)
    signs = []
    for i in range(4):
        a, b, c = p[i], p[(i + 1) % 4], p[(i + 2) % 4]
        signs.append(float(np.sign(np.cross(b - a, c - b))))
    nonzero = [s for s in signs if s != 0]
    return bool(nonzero) and all(s == nonzero[0] for s in nonzero)


def mm_from_px(pixels: float, px_per_mm: float) -> float:
    """Convert a pixel length to millimetres.

    The division the whole condition engine rests on. It lives here, once, so that raising
    the rectification scale cannot silently desynchronise measurement from the rubric's
    millimetre thresholds (docs/CONDITION.md).
    """
    if px_per_mm <= 0:
        raise ValueError("px_per_mm must be positive")
    return pixels / px_per_mm


def mm2_from_px2(pixel_area: float, px_per_mm: float) -> float:
    """Convert a pixel area to square millimetres. Note the square: doubling the scale
    quadruples the pixel count for the same physical defect."""
    if px_per_mm <= 0:
        raise ValueError("px_per_mm must be positive")
    return pixel_area / (px_per_mm * px_per_mm)


def px_from_mm(millimetres: float, px_per_mm: float) -> float:
    """Inverse of `mm_from_px`, for drawing a known physical size onto an image."""
    return millimetres * px_per_mm
