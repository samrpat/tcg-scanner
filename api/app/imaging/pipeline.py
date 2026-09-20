"""Orchestration: bytes in, a rectified card and a verdict out.

Pure with respect to the rest of the system — no database, no storage — so the whole pipeline
can be exercised against synthetic images.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from app.config import settings
from app.enums import QualityVerdict
from app.imaging import quality as quality_mod
from app.imaging.backref import register_back
from app.imaging.detect import Detection, detect_card
from app.imaging.dewarp import DewarpResult, dewarp, effective_px_per_mm
from app.imaging.geometry import quad_area, quad_aspect
from app.imaging.quality import QualityReport, assess_quality


def choose_scale(source_px_per_mm: float) -> float:
    """Pick the rectification scale for one capture.

    In `auto` mode the output tracks what the camera actually resolved, clamped to the
    configured range. Upscaling a 7 px/mm photo to 40 invents no detail and quadruples the
    file; downscaling an 80 px/mm photo to 20 discards detail that surface assessment wants.
    Matching the source keeps every real pixel and no fake ones.

    The scale is rounded to a whole number purely so stored values stay tidy — nothing
    depends on it being round, because each image records its own px_per_mm.
    """
    if settings.processed_scale_mode.lower() != "auto":
        return settings.processed_px_per_mm

    scale = round(source_px_per_mm)
    return float(
        min(
            max(scale, settings.processed_px_per_mm_min),
            settings.processed_px_per_mm_max,
        )
    )


@dataclass(frozen=True)
class ProcessResult:
    ok: bool
    detection: Detection | None
    dewarped: DewarpResult | None
    quality: QualityReport | None
    error: str | None = None

    @property
    def needs_review(self) -> bool:
        if not self.ok:
            return True
        if self.detection is None:
            return True
        if self.detection.confidence < settings.detection_min_confidence:
            return True
        return self.quality is not None and self.quality.verdict is not QualityVerdict.OK

    def review_reason(self) -> str | None:
        if self.error:
            return self.error
        if self.detection is None:
            return "no card detected in the image"
        reasons: list[str] = []
        if self.detection.confidence < settings.detection_min_confidence:
            reasons.append(
                f"low detection confidence {self.detection.confidence:.2f} "
                f"(threshold {settings.detection_min_confidence:.2f}, "
                f"method {self.detection.method})"
            )
        if self.quality:
            reasons.extend(self.quality.reasons)
        return "; ".join(reasons) or None


def decode_with_scale(payload: bytes) -> tuple[np.ndarray | None, float]:
    """Decode, and report the scale the result is at relative to the original file.

    Returns (image, scale) where scale is 1.0 or 0.5. Corners measured on the returned image are
    in *that* image's coordinates and must be divided by the scale before being stored or shown
    against the original — see `decode`.
    """
    image = _decode_at(payload)
    if image is None:
        return None, 1.0
    full = _dimensions(payload)
    if full is None or not full[0]:
        return image, 1.0
    return image, image.shape[1] / full[0]


def _dimensions(payload: bytes) -> tuple[int, int] | None:
    """Width and height of the encoded image, without decoding it at full size."""
    buffer = np.frombuffer(payload, dtype=np.uint8)
    probe = cv2.imdecode(buffer, cv2.IMREAD_REDUCED_COLOR_8)
    if probe is None or not probe.size:
        return None
    return probe.shape[1] * 8, probe.shape[0] * 8


def decode(payload: bytes) -> np.ndarray | None:
    """Decode image bytes to BGR, at half resolution when the capture can spare it.

    A modern phone hands over a 12 MP frame, and a card fills perhaps two thirds of it. Decoding
    that in full costs a 37 MB buffer and the detector then has four times the pixels to sweep,
    all to describe a card that is rectified to roughly 1,300 px across regardless.

    Measured over eight real captures, full vs half:

        full  decode 0.05s  detect 0.23s  total 0.27s  8/8 detected  mean confidence 0.925  37 MB
        half  decode 0.02s  detect 0.08s  total 0.10s  8/8 detected  mean confidence 0.946   9 MB

    Two and a half times faster, a quarter of the memory, and detection is *better* rather than
    worse — halving averages away sensor noise that the edge finder would otherwise chase. This
    is the single largest saving available on a Pi, and it costs nothing that grading needs.

    The guard matters: only halve when the result still comfortably exceeds the resolution a
    rectified card needs, so a small or distant capture is never degraded. libjpeg does the
    downscale during decode, so the full-size buffer is never allocated at all.
    """
    return _decode_at(payload)


def decode_full(payload: bytes) -> np.ndarray | None:
    """Decode at native resolution, ignoring the half-decode setting.

    Used for the final warp: the output is what a buyer looks at, so it is cut from every pixel
    the camera recorded.
    """
    buffer = np.frombuffer(payload, dtype=np.uint8)
    if buffer.size == 0:
        return None
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    return image if image is not None and image.size else None


def _decode_at(payload: bytes) -> np.ndarray | None:
    buffer = np.frombuffer(payload, dtype=np.uint8)
    if buffer.size == 0:
        return None

    flag = cv2.IMREAD_COLOR
    if settings.capture_half_decode:
        probe = cv2.imdecode(buffer, cv2.IMREAD_REDUCED_COLOR_8)
        # probe is 1/8 scale, so the half-scale size is probe * 4.
        if (
            probe is not None
            and probe.size
            and min(probe.shape[1], probe.shape[0]) * 4
            >= settings.capture_half_decode_min_px
        ):
            flag = cv2.IMREAD_REDUCED_COLOR_2

    image = cv2.imdecode(buffer, flag)
    return image if image is not None and image.size else None


def encode_jpeg(image: np.ndarray, quality: int | None = None) -> bytes:
    """Encode without chroma subsampling.

    The default 4:2:0 halves colour resolution, which smears precisely the fine colour edges
    that edge whitening and print-line defects consist of. 4:4:4 costs perhaps 15% more bytes
    and keeps them.
    """
    params = [
        int(cv2.IMWRITE_JPEG_QUALITY),
        int(quality if quality is not None else settings.processed_jpeg_quality),
        int(cv2.IMWRITE_JPEG_SAMPLING_FACTOR),
        int(cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444),
    ]
    ok, buffer = cv2.imencode(".jpg", image, params)
    if not ok:
        raise RuntimeError("failed to encode JPEG")
    return buffer.tobytes()


def _register_back_as_detection(image: np.ndarray) -> Detection | None:
    """Locate a card back by reference registration, as a Detection the pipeline understands."""
    registration = register_back(image)
    if registration is None:
        return None

    # Reference order, not image order: the first corner is the card's own top-left, so a back
    # photographed upside-down rectifies upright. See `dewarp(oriented=...)`.
    corners = np.asarray(registration.corners, dtype=np.float32).reshape(4, 2)
    height, width = image.shape[:2]
    return Detection(
        corners=corners,
        # Inlier ratio is a far better calibrated confidence than any shape heuristic: it is
        # the fraction of hundreds of feature matches that agree on one homography.
        confidence=round(min(1.0, registration.inlier_ratio), 4),
        method="backref",
        area_fraction=round(quad_area(corners) / float(height * width), 4),
        aspect=round(quad_aspect(corners), 4),
        rectangularity=1.0,
        edge_fit=registration.as_dict(),
    )


def process_capture(payload: bytes, px_per_mm: float | None = None) -> ProcessResult:
    """Detect, rectify and assess one captured image.

    `px_per_mm` overrides the configured scale; leave it unset to let `choose_scale` match the
    capture.
    """
    image, decode_scale = decode_with_scale(payload)
    if image is None:
        return ProcessResult(
            ok=False, detection=None, dewarped=None, quality=None,
            error="could not decode the uploaded image",
        )

    # A card back is the same image every time, so try registering it against a reference
    # before falling back to finding edges. Registration is strictly better where it applies:
    # it recovers the corners from hundreds of matched features rather than four, so the result
    # is square rather than skewed; it knows which way up the card is; and it cannot be fooled
    # by the pokéball, because it is matching the whole design rather than looking for a
    # rectangle. Measured on ten real captures: 10/10 backs registered, 0/10 fronts falsely
    # registered, ~25ms each.
    detection = _register_back_as_detection(image) or detect_card(image)
    if detection is None:
        # Still report quality on the whole frame: "blurry and no card" is more useful
        # feedback to the operator than "no card".
        return ProcessResult(
            ok=False,
            detection=None,
            dewarped=None,
            quality=assess_quality(image, None),
            error="no card detected in the image",
        )

    # Find the card on the small image; cut it from the full one.
    #
    # These are two different jobs with opposite requirements. Locating four corners tolerates a
    # halved image happily — measured, detection is if anything *better* at half size, because
    # halving averages away the sensor noise the edge finder would otherwise chase. Producing
    # the picture a buyer will zoom into does not tolerate it at all: every pixel thrown away
    # before the warp is detail that cannot come back.
    #
    # So corners are scaled back into the original file's coordinates and the warp samples the
    # original. Detection keeps its speed, output keeps its quality.
    #
    # Storing corners in the ORIGINAL file's space is also the only coordinate system that means
    # anything to anyone else. They are persisted, redisplayed in the corner editor over the
    # full-size original, and sent back by the operator. Leaving them in a decoder-dependent
    # space put every stored quad in the top-left quadrant of the picture it was drawn on.
    if decode_scale != 1.0:
        detection = detection.scaled(1.0 / decode_scale)
        full = decode_full(payload)
        if full is not None:
            image = full

    source_scale = effective_px_per_mm(detection.corners)
    scale = px_per_mm if px_per_mm is not None else choose_scale(source_scale)

    try:
        # Registration-derived corners carry the card's own orientation; detector-derived ones
        # only carry image position, so they must still be ordered.
        result = dewarp(
            image,
            detection.corners,
            scale,
            contour=detection.contour,
            oriented=detection.method in ("backref", "artref"),
        )
    except Exception as exc:  # noqa: BLE001 - a warp failure must not lose the original
        return ProcessResult(
            ok=False, detection=detection, dewarped=None,
            quality=assess_quality(image, source_scale),
            error=f"dewarp failed: {exc}",
        )

    # Assess the rectified card, not the whole frame: background clutter should not count
    # against the capture, and glare on the card is what actually matters. The margin is
    # measured against the ORIGINAL frame, since that is where cropping happens.
    height, width = image.shape[:2]
    margin = quality_mod.frame_margin(detection.corners, width, height)
    quality = assess_quality(
        result.image,
        source_scale,
        margin_px=margin,
        area_fraction=detection.area_fraction,
    )

    return ProcessResult(ok=True, detection=detection, dewarped=result, quality=quality)
