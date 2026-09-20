"""Serve stored images, full size or as a thumbnail.

Paths are validated by the storage layer, which refuses anything that would escape the root.

The listing renders are 2–3 MB each and there are six per card, so a screen showing a pile of
them was fetching tens of megabytes to draw pictures a few hundred pixels wide. `?w=` returns a
resized copy instead, which is the difference between a grid that appears and a grid that
gradually arrives.

Thumbnails are generated on demand and cached on disk beside nothing — they are derived, so a
lost cache costs one resize rather than any data.
"""

import hashlib
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query, Response

from app.logging_setup import get_logger
from app.storage import UnsafePathError, get_storage

router = APIRouter(prefix="/images", tags=["images"])
log = get_logger(__name__)

# Widths the UI actually asks for. An open list would let a caller fill the disk with one
# thumbnail per pixel width.
ALLOWED_WIDTHS = (240, 400, 800, 1200)

THUMBNAIL_QUALITY = 88
_CACHE = Path("/data/thumbnails")


def _thumbnail(payload: bytes, width: int, key: str) -> bytes:
    """Resize, remembering the result. Falls back to the original if anything goes wrong."""
    cached = _CACHE / f"{key}-{width}.jpg"
    try:
        if cached.exists():
            return cached.read_bytes()
    except OSError:
        pass

    try:
        image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return payload
        height, original_width = image.shape[:2]
        if original_width <= width:
            return payload
        scale = width / original_width
        # INTER_AREA is the right filter for shrinking; the defaults alias on fine detail,
        # which on these images is exactly the edge wear the picture exists to show.
        small = cv2.resize(
            image, (width, max(1, int(height * scale))), interpolation=cv2.INTER_AREA
        )
        ok, encoded = cv2.imencode(
            ".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), THUMBNAIL_QUALITY]
        )
        if not ok:
            return payload
        data = encoded.tobytes()
    except Exception as exc:  # noqa: BLE001 - a thumbnail is a nicety, the image is not
        log.warning("thumbnail.failed", error=str(exc))
        return payload

    try:
        _CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    except OSError:
        pass  # Serving it uncached is fine; it is only slower.
    return data


@router.get("/{path:path}")
async def get_image(path: str, w: int | None = Query(None)) -> Response:
    storage = get_storage()
    try:
        if not storage.exists(path):
            raise HTTPException(status_code=404, detail="image not found")
        payload = storage.get(path)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if w is not None:
        if w not in ALLOWED_WIDTHS:
            raise HTTPException(
                status_code=422,
                detail=f"width must be one of {', '.join(map(str, ALLOWED_WIDTHS))}",
            )
        # Keyed on the content, so a re-rendered image never serves its predecessor's
        # thumbnail — which is precisely what would happen after adjusting a crop.
        key = hashlib.sha256(payload).hexdigest()[:24]
        payload = _thumbnail(payload, w, key)

    # Safe to serve immutable because callers are handed content-addressed URLs
    # (`?v=<sha256>`); see `image_url` in the capture router. A bare path is served the same
    # way, so link to images through that helper rather than constructing paths by hand.
    return Response(
        content=payload,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
