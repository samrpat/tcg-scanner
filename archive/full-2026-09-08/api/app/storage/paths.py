"""SKU and object-path construction.

Layout is fixed by spec §20:

    CARD-000001/
        original-front.jpg
        original-back.jpg
        processed-front.jpg
        processed-back.jpg
"""

import re

from app.enums import ImageKind

SKU_PREFIX = "CARD"
SKU_DIGITS = 6
SKU_RE = re.compile(rf"^{SKU_PREFIX}-\d{{{SKU_DIGITS},}}$")

_FILENAMES: dict[ImageKind, str] = {
    ImageKind.ORIGINAL_FRONT: "original-front.jpg",
    ImageKind.ORIGINAL_BACK: "original-back.jpg",
    ImageKind.PROCESSED_FRONT: "processed-front.jpg",
    ImageKind.PROCESSED_BACK: "processed-back.jpg",
    ImageKind.LISTING_FRONT: "listing-front.jpg",
    ImageKind.LISTING_BACK: "listing-back.jpg",
    ImageKind.DETAIL_FRONT_TL: "detail-front-tl.jpg",
    ImageKind.DETAIL_FRONT_TR: "detail-front-tr.jpg",
    ImageKind.DETAIL_FRONT_BL: "detail-front-bl.jpg",
    ImageKind.DETAIL_FRONT_BR: "detail-front-br.jpg",
}


class UnsafePathError(ValueError):
    """Raised when a SKU or path would escape the storage root."""


def format_sku(number: int) -> str:
    if number < 1:
        raise ValueError("SKU numbers start at 1")
    return f"{SKU_PREFIX}-{number:0{SKU_DIGITS}d}"


def parse_sku(sku: str) -> int:
    validate_sku(sku)
    return int(sku.split("-", 1)[1])


def validate_sku(sku: str) -> None:
    """Reject anything that is not exactly a SKU.

    This is the traversal guard: SKUs are the only user-influenced part of a storage path,
    so nothing that fails this pattern ever reaches the filesystem (REQ-STO-005).
    """
    if not isinstance(sku, str) or not SKU_RE.match(sku):
        raise UnsafePathError(f"invalid SKU: {sku!r}")


def item_dir(sku: str) -> str:
    validate_sku(sku)
    return sku


def image_path(sku: str, kind: ImageKind) -> str:
    validate_sku(sku)
    try:
        filename = _FILENAMES[kind]
    except KeyError as exc:  # pragma: no cover - guards a programming error
        raise ValueError(f"unknown image kind: {kind}") from exc
    return f"{sku}/{filename}"


def is_original(kind: ImageKind) -> bool:
    """Originals are written once and never touched again (REQ-STO-002)."""
    return kind in (ImageKind.ORIGINAL_FRONT, ImageKind.ORIGINAL_BACK)
