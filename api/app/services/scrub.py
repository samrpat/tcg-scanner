"""Strip identifying metadata out of a photograph, without touching its pixels.

A photograph taken on a phone carries a great deal that is not the picture: where it was taken,
on what, when, at what shutter speed, sometimes a serial number. A card scanner has no use for
any of it, and this one has two ways of handing it to strangers — the batch export offers the
originals under `kind=all`, and the public photo host serves
`/api/images/CARD-000001/original-front.jpg` to anybody with the address.

Captures taken through the app's own camera are already clean: the browser draws the frame to a
canvas, and a canvas has no metadata to give. **Uploads are the hole.** Somebody pointing the
Capture screen at their camera roll uploads the file as the phone wrote it, GPS and all, and it
is stored and served exactly as received.

## Lossless, deliberately

The obvious fix is to re-encode with Pillow and drop the metadata on the way. That re-compresses
a JPEG, and originals are the input to detection and to every future reprocess — quality lost
here is lost for good, on the one copy that is supposed to be pristine.

So this edits the container instead of the image: a JPEG is a sequence of marker segments, and
the metadata lives in its own segments alongside the compressed scan data. Dropping those
segments leaves every byte of the actual picture untouched.

## What is kept

- **JFIF** (APP0) — density and thumbnail info the format expects.
- **ICC colour profile** (APP2) — dropping it shifts colour, and colour is part of judging a
  card.
- **Orientation** — rebuilt into a minimal EXIF block if the original had one. The pipeline does
  not care (OpenCV reads pixels and ignores the tag) but anything opening the exported original
  does, and a card lying on its side is a worse export than one with no metadata.

Everything else goes: GPS, camera make and model, serial numbers, timestamps, software, XMP,
IPTC, MakerNote.
"""

from __future__ import annotations

import io

from PIL import Image

from app.logging_setup import get_logger

log = get_logger(__name__)

# Application segments that carry metadata rather than picture.
#   APP1  EXIF (and XMP, which uses the same marker with a different header)
#   APP13 Photoshop / IPTC
# APP0 (JFIF) and APP2 (ICC) are kept; see the module docstring.
_DROP = {0xFFE1, 0xFFED}

# Segments with no length field. Everything from SOS onward is entropy-coded scan data and is
# copied verbatim.
_STANDALONE = {0xFFD8, 0xFFD9, *range(0xFFD0, 0xFFD8)}

_EXIF_ORIENTATION = 0x0112


def _orientation(payload: bytes) -> int:
    """The EXIF orientation, or 1 when there is none or it is unreadable."""
    try:
        with Image.open(io.BytesIO(payload)) as image:
            return int(image.getexif().get(_EXIF_ORIENTATION, 1) or 1)
    except Exception:  # noqa: BLE001 - an unreadable tag is simply no tag
        return 1


def _orientation_segment(orientation: int) -> bytes | None:
    """A minimal EXIF APP1 segment carrying nothing but the orientation."""
    if orientation in (0, 1):
        return None
    exif = Image.Exif()
    exif[_EXIF_ORIENTATION] = orientation
    body = exif.tobytes()
    if not body:
        return None
    # Pillow emits the segment payload complete with its "Exif\0\0" header.
    return b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body


def scrub_jpeg(payload: bytes) -> tuple[bytes, list[str]]:
    """Return the JPEG with metadata segments removed, and what was removed.

    Anything that is not a JPEG, or is malformed, comes back untouched. Refusing to store a
    photograph because its container confused this would be a far worse failure than keeping
    some metadata — the picture is the point.
    """
    if not payload.startswith(b"\xff\xd8"):
        return payload, []

    kept = bytearray(b"\xff\xd8")
    removed: list[str] = []
    orientation = _orientation(payload)

    # The orientation block goes first, where a reader expects EXIF to be.
    block = _orientation_segment(orientation)
    if block:
        kept += block

    i = 2
    total = len(payload)
    try:
        while i < total:
            if payload[i] != 0xFF:
                # Not on a marker boundary: something is unusual. Keep the rest verbatim.
                kept += payload[i:]
                break
            marker = int.from_bytes(payload[i : i + 2], "big")

            if marker == 0xFFDA:  # start of scan — the rest is the picture
                kept += payload[i:]
                break
            if marker in _STANDALONE:
                kept += payload[i : i + 2]
                i += 2
                continue

            length = int.from_bytes(payload[i + 2 : i + 4], "big")
            if length < 2 or i + 2 + length > total:
                kept += payload[i:]
                break

            if marker in _DROP:
                removed.append(f"APP{marker - 0xFFE0}")
            else:
                kept += payload[i : i + 2 + length]
            i += 2 + length
    except Exception:  # noqa: BLE001 - see docstring; never lose the photograph
        log.warning("scrub.failed_parsing", bytes=len(payload))
        return payload, []

    return bytes(kept), removed


def scrub(payload: bytes) -> bytes:
    """Metadata-free bytes, for storing. Logs what came out."""
    cleaned, removed = scrub_jpeg(payload)
    if removed:
        log.info(
            "scrub.removed",
            segments=",".join(sorted(set(removed))),
            saved_bytes=len(payload) - len(cleaned),
        )
    return cleaned
