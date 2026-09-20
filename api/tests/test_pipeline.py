"""End-to-end: JPEG bytes in, rectified card and verdict out."""

import cv2
import numpy as np
import pytest

from app.enums import QualityVerdict
from app.imaging.pipeline import decode, encode_jpeg, process_capture
from tests.synthetic import make_card, place_on_background, to_jpeg


def test_a_good_capture_goes_straight_through():
    scene, _ = place_on_background(make_card(px_per_mm=16.0))
    result = process_capture(to_jpeg(scene), px_per_mm=20.0)

    assert result.ok
    assert result.detection is not None
    assert result.dewarped is not None
    assert (result.dewarped.width, result.dewarped.height) == (1260, 1760)
    assert result.quality is not None
    assert not result.needs_review
    assert result.review_reason() is None


def test_no_card_fails_cleanly_with_a_reason():
    empty = np.full((900, 1200, 3), 30, dtype=np.uint8)
    result = process_capture(to_jpeg(empty))

    assert not result.ok
    assert result.detection is None
    assert result.dewarped is None
    assert result.needs_review
    assert "no card detected" in (result.review_reason() or "")
    # Quality is still reported, so the operator learns *why* nothing was found.
    assert result.quality is not None


def test_undecodable_bytes_do_not_raise():
    result = process_capture(b"this is not an image")
    assert not result.ok
    assert result.needs_review
    assert "decode" in (result.error or "")


def test_empty_payload_does_not_raise():
    assert not process_capture(b"").ok


def test_a_low_resolution_capture_is_flagged_for_review():
    """Detection succeeds, but the source has too little detail to grade from."""
    scene, _ = place_on_background(make_card(px_per_mm=3.0), scene_size=(400, 300))
    result = process_capture(to_jpeg(scene), px_per_mm=20.0)

    if result.ok:
        assert result.quality is not None
        assert result.quality.verdict is QualityVerdict.REJECT
        assert result.needs_review


def test_quality_is_judged_on_the_card_not_the_background():
    """A cluttered or dark background should not count against an otherwise good capture."""
    card = make_card(px_per_mm=16.0)
    scene, _ = place_on_background(card, background=(0, 0, 0), noise=0)
    result = process_capture(to_jpeg(scene), px_per_mm=20.0)

    assert result.ok
    assert result.quality is not None
    # The frame is mostly black, but the card is not, so shadows must not be flagged.
    assert result.quality.crushed_shadows < 0.05


def test_decode_round_trip():
    card = make_card()
    decoded = decode(encode_jpeg(card))
    assert decoded is not None
    assert decoded.shape == card.shape


def test_decode_rejects_junk():
    assert decode(b"") is None
    assert decode(b"\x00\x01\x02\x03") is None


def test_detection_confidence_is_reported_for_review_routing():
    scene, _ = place_on_background(make_card(px_per_mm=16.0))
    result = process_capture(to_jpeg(scene))
    assert result.detection is not None
    assert 0.0 < result.detection.confidence <= 1.0
    assert len(result.detection.as_list()) == 4


def test_reprocessing_the_same_bytes_is_deterministic():
    scene, _ = place_on_background(make_card(px_per_mm=16.0))
    payload = to_jpeg(scene)
    first = process_capture(payload)
    second = process_capture(payload)

    assert first.detection is not None and second.detection is not None
    assert first.detection.confidence == second.detection.confidence
    assert np.array_equal(first.dewarped.image, second.dewarped.image)


def test_output_is_a_valid_jpeg():
    scene, _ = place_on_background(make_card(px_per_mm=16.0))
    result = process_capture(to_jpeg(scene))
    assert result.dewarped is not None

    encoded = encode_jpeg(result.dewarped.image)
    reloaded = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
    assert reloaded is not None
    # Dimensions follow the chosen scale, not a constant. What must hold is the card's shape.
    assert reloaded.shape == (result.dewarped.height, result.dewarped.width, 3)
    assert reloaded.shape[0] / reloaded.shape[1] == pytest.approx(88 / 63, rel=0.01)


def test_the_scale_follows_the_source_rather_than_a_constant():
    """A capture is rectified at roughly the resolution the camera actually achieved, so no
    detail is discarded and none is invented."""
    from app.imaging.dewarp import effective_px_per_mm

    scene, _ = place_on_background(make_card(px_per_mm=16.0))
    result = process_capture(to_jpeg(scene))
    assert result.ok and result.detection is not None and result.dewarped is not None

    source = effective_px_per_mm(result.detection.corners)
    assert result.dewarped.px_per_mm == pytest.approx(round(source), abs=1)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (3.0, 10.0),    # clamped up to the floor
        (7.6, 10.0),    # the real hand-held capture: below the floor
        (18.4, 18.0),   # a card filling a 1080p frame
        (43.0, 43.0),   # a 12MP phone filling the frame
        (120.0, 80.0),  # clamped down to the ceiling
    ],
)
def test_choose_scale_clamps_to_the_configured_range(source, expected):
    from app.imaging.pipeline import choose_scale

    assert choose_scale(source) == expected


def test_choose_scale_honours_fixed_mode(monkeypatch):
    from app.config import settings
    from app.imaging.pipeline import choose_scale

    monkeypatch.setattr(settings, "processed_scale_mode", "fixed")
    assert choose_scale(7.0) == settings.processed_px_per_mm
    assert choose_scale(200.0) == settings.processed_px_per_mm


def test_jpeg_encoding_keeps_full_chroma():
    """4:2:0 subsampling halves colour resolution, smearing the fine colour edges that
    whitening and print defects consist of."""
    card = make_card(px_per_mm=20.0)
    encoded = encode_jpeg(card)
    # The sampling factor lives in the SOF0 marker's component table. 0x11 per component is
    # 4:4:4; 0x22 on the luma component would mean 4:2:0.
    sof = encoded.find(b"\xff\xc0")
    assert sof != -1
    components = encoded[sof + 10 : sof + 19]
    assert components[1] == 0x11, "luma sampling factor should be 1x1 (4:4:4)"


def test_grid_hash_keeps_layout_that_a_single_hash_averages_away():
    """A 64-bit perceptual hash summarises the whole card, so two cards with similar overall
    tone but different layouts collide. Hashing a 4x4 grid keeps where things are.

    On real captures this took rank-1 recall from 7/12 to 12/12 across 21,775 cards, and both
    cards the single hash pushed outside the top 20 — where registration would never have seen
    them — came first.
    """
    from app.services.hashing import GRID, grid_phash, phash

    base = make_card(px_per_mm=16.0)

    # Same content, rearranged: a single hash sees almost the same card, a grid hash does not.
    shuffled = base.copy()
    height = base.shape[0]
    shuffled[: height // 2], shuffled[height // 2 :] = (
        base[height // 2 :].copy(),
        base[: height // 2].copy(),
    )

    single_distance = bin(phash(base) ^ phash(shuffled)).count("1")

    grid_a, grid_b = grid_phash(base), grid_phash(shuffled)
    assert len(grid_a) == GRID * GRID * 8
    grid_distance = sum(bin(x ^ y).count("1") for x, y in zip(grid_a, grid_b, strict=True))

    assert grid_distance > single_distance, (
        "the grid hash must notice a layout change the single hash averages away"
    )


def test_grid_hash_is_stable_for_the_same_image():
    from app.services.hashing import grid_phash

    card = make_card(px_per_mm=16.0)
    assert grid_phash(card) == grid_phash(card.copy())
