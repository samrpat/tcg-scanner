"""Rectify a detected card to a known physical rectangle.

Geometry only. No colour, brightness or contrast is altered — condition assessment reads
whitening and gloss straight off these pixels, and auto-levels would erase edge whitening
entirely (docs/IMAGING.md).
"""

from dataclasses import dataclass

import cv2
import numpy as np

from app.imaging.geometry import order_corners, output_size, quad_aspect


@dataclass(frozen=True)
class DewarpResult:
    image: np.ndarray
    px_per_mm: float
    rotation_applied: int  # degrees, counter-clockwise
    width: int
    height: int
    # "homography" for the straight-edge transform, "coons" when the card's traced outline was
    # followed instead. Recorded because it changes how exact the border is.
    method: str = "homography"
    # Worst deviation of the card's real edge from a straight line, in source pixels. This is
    # lens distortion plus any bow in the card, and it is what the Coons path removes.
    edge_bow_px: float = 0.0


def dewarp(
    image: np.ndarray,
    corners: np.ndarray,
    px_per_mm: float,
    contour: np.ndarray | None = None,
    oriented: bool = False,
) -> DewarpResult:
    """Warp the card at `corners` onto a portrait rectangle at `px_per_mm`.

    When the traced `contour` is supplied and the card's edges are measurably bowed — lens
    distortion, or a card that is not flat — the warp follows the real outline via a Coons
    patch rather than drawing straight lines between the corners. That is what makes the
    rectified border land exactly on the card's border instead of drifting in the middle of an
    edge, which is visible in a listing photograph.

    A card photographed sideways is detected as a landscape quad. Warping that straight onto a
    portrait canvas would squash it, so the warp targets a landscape canvas first and the
    result is rotated. Same pixels, correct proportions.

    `oriented` says the caller's corners are already in the card's own order — first corner is
    the card's top-left, whichever way up it was photographed. That is exactly what projecting a
    reference image's corners through a homography produces, and it makes orientation correction
    free: a card shot upside-down comes out upright because the reference's top-left is mapped
    to the output's top-left. Ordering by image position instead, as the default does, throws
    that information away and rectifies the card upside-down.
    """
    width_px, height_px = output_size(px_per_mm)
    # Order defensively rather than trusting the caller. `order_corners` is idempotent, and
    # an unordered quad warps to silently wrong geometry — the kind of failure that produces
    # a plausible-looking image and mis-measures every defect on it. The exception is a caller
    # that knows the card's own orientation; see `oriented`.
    quad = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    source = quad if oriented else order_corners(quad)

    # With caller-supplied orientation the corner order already encodes which way up the card
    # is: mapping those corners onto a portrait rectangle rotates the content by itself. Testing
    # the quad's shape as well would rotate a second time, which is what turned a card
    # photographed sideways into a sideways crop.
    landscape = False if oriented else quad_aspect(source) < 1.0
    target_w, target_h = (height_px, width_px) if landscape else (width_px, height_px)

    destination = np.array(
        [[0, 0], [target_w - 1, 0], [target_w - 1, target_h - 1], [0, target_h - 1]],
        dtype=np.float32,
    )

    method = "homography"
    edge_bow = 0.0
    warped = None

    if warped is None:
        matrix = cv2.getPerspectiveTransform(source, destination)
        warped = cv2.warpPerspective(
            image,
            matrix,
            (target_w, target_h),
            # INTER_CUBIC preserves fine surface texture better than INTER_LINEAR, which
            # matters because that texture is the signal for scuffing and scratches.
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )

    rotation = 0
    if landscape:
        warped = cv2.rotate(warped, cv2.ROTATE_90_COUNTERCLOCKWISE)
        rotation = 90

    height, width = warped.shape[:2]
    return DewarpResult(
        image=warped,
        px_per_mm=px_per_mm,
        rotation_applied=rotation,
        width=width,
        height=height,
        method=method,
        edge_bow_px=round(edge_bow, 2),
    )


def effective_px_per_mm(corners: np.ndarray) -> float:
    """Actual scale of the SOURCE capture, in pixels per millimetre.

    This is the number that says whether the photograph carries enough real detail to measure
    a 2.5mm² defect. The rectified image always reports the configured px_per_mm, but
    upscaling a small crop invents no information — so quality is judged on this instead.
    """
    from app.imaging.geometry import CARD_HEIGHT_MM, side_lengths

    ordered = order_corners(np.asarray(corners, dtype=np.float32).reshape(4, 2))
    top, right, bottom, left = side_lengths(ordered)
    width_px = (top + bottom) / 2
    height_px = (left + right) / 2

    # Use the long edge against the long dimension so a sideways card is measured correctly.
    if height_px >= width_px:
        return float(height_px / CARD_HEIGHT_MM)
    return float(width_px / CARD_HEIGHT_MM)
