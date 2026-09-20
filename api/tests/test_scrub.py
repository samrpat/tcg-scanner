"""Metadata must not survive into storage.

A photograph from a phone carries where it was taken. This application has two ways of handing
that to strangers — the batch export offers the originals under `kind=all`, and the public
photo host serves `/api/images/CARD-000001/original-front.jpg` to anyone with the address.

Captures through the app's own camera are already clean, because a canvas has no metadata to
give. Uploads are the hole these tests cover.
"""

import io
from fractions import Fraction

import numpy as np
import pytest
from PIL import Image

from app.services.scrub import scrub, scrub_jpeg

MAKE, MODEL, SOFTWARE, TAKEN = 0x010F, 0x0110, 0x0131, 0x9003
ORIENTATION, GPS = 0x0112, 0x8825


def photograph(*, orientation: int = 1, gps: bool = True) -> bytes:
    """A JPEG as a phone would write it."""
    pixels = (np.random.default_rng(11).random((160, 120, 3)) * 255).astype("uint8")
    exif = Image.Exif()
    exif[MAKE] = "ACME Phone Co"
    exif[MODEL] = "ACME X9 Pro"
    exif[SOFTWARE] = "ACME Camera 4.2"
    exif[TAKEN] = "2026:09:19 21:14:07"
    exif[ORIENTATION] = orientation
    if gps:
        exif[GPS] = {
            1: "N",
            2: (Fraction(51), Fraction(30), Fraction(2613, 100)),
            3: "W",
            4: (Fraction(0), Fraction(7), Fraction(3456, 100)),
        }
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="JPEG", quality=92, exif=exif.tobytes())
    return buffer.getvalue()


def exif_of(blob: bytes):
    return Image.open(io.BytesIO(blob)).getexif()


def test_location_does_not_survive():
    """The one that matters. A card photograph should not say which room it was taken in."""
    before = photograph()
    assert exif_of(before).get_ifd(GPS), "fixture is wrong — no GPS to remove"

    after = scrub(before)
    assert not exif_of(after).get_ifd(GPS)


@pytest.mark.parametrize("tag", [MAKE, MODEL, SOFTWARE, TAKEN])
def test_the_device_and_the_moment_do_not_survive(tag):
    """Camera make, model, software and timestamp are all identifying and none are useful to a
    scanner."""
    assert exif_of(photograph()).get(tag) is not None
    assert exif_of(scrub(photograph())).get(tag) is None


def test_the_pixels_are_untouched():
    """Lossless on purpose. Originals are the input to detection and to every future
    reprocess; quality lost here is lost on the one copy meant to be pristine.

    Re-encoding with Pillow to drop metadata would have been three lines and would have
    recompressed every capture.
    """
    before = photograph()
    after = scrub(before)
    assert len(after) < len(before)  # something came out
    assert np.array_equal(
        np.asarray(Image.open(io.BytesIO(before))),
        np.asarray(Image.open(io.BytesIO(after))),
    )


def test_orientation_survives():
    """The pipeline ignores it — OpenCV reads pixels — but anything opening an exported
    original does not, and a card lying on its side is a worse export than a bare one."""
    after = scrub(photograph(orientation=6))
    assert exif_of(after).get(ORIENTATION) == 6


def test_an_image_with_nothing_to_remove_is_left_alone():
    after, removed = scrub_jpeg(photograph(gps=False, orientation=1))
    assert removed == ["APP1"]  # the block still held make/model/software
    assert exif_of(after).get(MAKE) is None


@pytest.mark.parametrize(
    "payload",
    [b"", b"not a jpeg at all", b"\xff\xd8truncated", b"\xff\xd8\xff\xe1\xff\xff"],
)
def test_rubbish_is_returned_unharmed(payload):
    """Refusing to store a photograph because its container confused the scrubber would be a
    far worse failure than keeping some metadata. The picture is the point."""
    out, removed = scrub_jpeg(payload)
    assert out == payload
    assert removed == []


def test_every_stored_original_goes_through_it():
    """The guard against this being wired into one upload path and not another."""
    import inspect

    from app.services import capture

    source = inspect.getsource(capture.attach_image)
    assert "scrub(payload)" in source
    # Before the write, not after.
    assert source.index("scrub(payload)") < source.index("storage.put")
