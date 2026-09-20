"""Image pipeline: detect the card, rectify it to a known physical size, judge the capture.

Everything here operates on numpy arrays and returns plain dataclasses. No database, no storage,
no HTTP — so the whole pipeline is testable against synthetic images (REQ-TST-001).
"""

from app.imaging.detect import Detection, detect_card
from app.imaging.dewarp import DewarpResult, dewarp
from app.imaging.geometry import (
    CARD_ASPECT,
    CARD_HEIGHT_MM,
    CARD_WIDTH_MM,
    mm2_from_px2,
    mm_from_px,
    order_corners,
    output_size,
    px_from_mm,
    quad_area,
)
from app.imaging.pipeline import ProcessResult, choose_scale, process_capture
from app.imaging.quality import QualityReport, assess_quality

__all__ = [
    "CARD_ASPECT",
    "CARD_HEIGHT_MM",
    "CARD_WIDTH_MM",
    "Detection",
    "DewarpResult",
    "ProcessResult",
    "QualityReport",
    "assess_quality",
    "choose_scale",
    "detect_card",
    "dewarp",
    "mm2_from_px2",
    "mm_from_px",
    "order_corners",
    "output_size",
    "process_capture",
    "px_from_mm",
    "quad_area",
]
