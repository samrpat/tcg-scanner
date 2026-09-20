"""Synthetic card generation for imaging tests.

Real photographs make poor tests: there is no ground truth to compare against. A card put
through a *known* homography has exact expected corners and an exact expected rectification,
so the pipeline can be measured rather than eyeballed.
"""

import cv2
import numpy as np

from app.imaging.geometry import CARD_HEIGHT_MM, CARD_WIDTH_MM


def make_card(px_per_mm: float = 12.0) -> np.ndarray:
    """A card-shaped image with enough structure to detect misalignment.

    Deliberately busy: a flat colour would score identically however it was warped, so the
    asymmetric marks are what make a rectification test meaningful.
    """
    width = int(round(CARD_WIDTH_MM * px_per_mm))
    height = int(round(CARD_HEIGHT_MM * px_per_mm))
    card = np.full((height, width, 3), (235, 225, 205), dtype=np.uint8)

    # Yellow-ish border, like a Pokémon card.
    cv2.rectangle(card, (0, 0), (width - 1, height - 1), (60, 200, 240), max(2, width // 24))
    # Art box in the upper half.
    cv2.rectangle(
        card,
        (width // 8, height // 8),
        (width - width // 8, height // 2),
        (150, 90, 40),
        -1,
    )
    # An asymmetric marker so an upside-down or mirrored result is detectable.
    cv2.circle(card, (width // 5, height // 5), max(4, width // 14), (40, 40, 220), -1)
    cv2.putText(
        card, "4/102", (width // 8, height - height // 12),
        cv2.FONT_HERSHEY_SIMPLEX, width / 420, (20, 20, 20), max(1, width // 300)
    )
    # Text-block texture in the lower half.
    for i in range(6):
        y = height // 2 + height // 20 + i * height // 26
        cv2.line(
            card, (width // 8, y), (width - width // 6, y), (90, 90, 90), max(1, height // 500)
        )
    return card


def card_quad(
    center: tuple[float, float],
    height_px: float,
    rotation_deg: float = 0.0,
    perspective: float = 0.0,
    landscape: bool = False,
) -> np.ndarray:
    """Ground-truth corners for a card-shaped quad.

    Generated rather than hand-written, because a hand-written quad is easy to get subtly
    wrong — an aspect of 1.08 instead of 1.40 is invisible on inspection but is genuinely
    not a card, and the detector is right to distrust it.

    `perspective` tilts the top edge inward as a fraction of width, imitating a photo taken
    from slightly above. `landscape` lays the card on its side.
    """
    width_px = height_px * CARD_WIDTH_MM / CARD_HEIGHT_MM
    if landscape:
        # A card lying on its side: same rectangle, swapped axes, corners still in canonical
        # TL/TR/BR/BL order. Rotating the quad by 90 degrees instead would also rotate which
        # physical corner comes first, which is a different thing to test.
        width_px, height_px = height_px, width_px
    half_w, half_h = width_px / 2, height_px / 2

    inset = perspective * width_px
    local = np.array(
        [
            [-half_w + inset, -half_h],
            [half_w - inset, -half_h],
            [half_w, half_h],
            [-half_w, half_h],
        ],
        dtype=np.float32,
    )

    theta = np.radians(rotation_deg)
    rotation = np.array(
        [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]], dtype=np.float32
    )
    return (local @ rotation.T + np.array(center, dtype=np.float32)).astype(np.float32)


def place_on_background(
    card: np.ndarray,
    scene_size: tuple[int, int] = (1600, 1200),
    corners: np.ndarray | None = None,
    background: tuple[int, int, int] = (35, 38, 42),
    noise: float = 3.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Project `card` onto a dark background at `corners`.

    Returns (scene, corners). The corners are the ground truth the detector must recover.
    """
    scene_w, scene_h = scene_size
    scene = np.full((scene_h, scene_w, 3), background, dtype=np.uint8)

    if corners is None:
        # A plausible hand-held framing: filling most of the frame, slightly rotated,
        # with a touch of perspective.
        corners = card_quad(
            center=(scene_w / 2, scene_h / 2),
            height_px=scene_h * 0.86,
            rotation_deg=4.0,
            perspective=0.02,
        )
    corners = np.asarray(corners, dtype=np.float32).reshape(4, 2)

    height, width = card.shape[:2]
    source = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(source, corners)
    warped = cv2.warpPerspective(card, matrix, (scene_w, scene_h))

    mask = np.zeros((scene_h, scene_w), dtype=np.uint8)
    cv2.fillConvexPoly(mask, corners.astype(np.int32), 255)
    scene[mask > 0] = warped[mask > 0]

    if noise:
        rng = np.random.default_rng(1234)
        scene = np.clip(
            scene.astype(np.float32) + rng.normal(0, noise, scene.shape), 0, 255
        ).astype(np.uint8)
    return scene, corners


def to_jpeg(image: np.ndarray, quality: int = 95) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    assert ok
    return buffer.tobytes()


def corner_error(actual: np.ndarray, expected: np.ndarray) -> float:
    """Worst per-corner distance in pixels, comparing like with like.

    Both quads are put through `order_corners` first. A quad generated with 90 degrees of
    rotation lists its corners starting from a different physical corner, and comparing that
    against the detector's TL/TR/BR/BL output would report a spurious ~1000px error.
    """
    from app.imaging.geometry import order_corners

    a = np.asarray(order_corners(actual), dtype=np.float64)
    e = np.asarray(order_corners(expected), dtype=np.float64)
    return float(np.max(np.linalg.norm(a - e, axis=1)))


def barrel_distort(image: np.ndarray, k1: float = -0.12) -> np.ndarray:
    """Apply radial lens distortion.

    Negative k1 is barrel: straight lines bow outward from the centre, which is what a phone's
    wide lens does and what a four-point homography cannot represent.
    """
    height, width = image.shape[:2]
    focal = max(width, height)
    camera = np.array(
        [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]], dtype=np.float64
    )
    coefficients = np.array([k1, 0.0, 0.0, 0.0], dtype=np.float64)

    # `initUndistortRectifyMap` maps undistorted -> distorted, so using it directly as a remap
    # of the clean image produces a distorted one.
    map_x, map_y = cv2.initUndistortRectifyMap(
        camera, coefficients, None, camera, (width, height), cv2.CV_32FC1
    )
    return cv2.remap(image, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
