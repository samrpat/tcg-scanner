"""Capture quality gates."""

import cv2
import numpy as np
import pytest

from app.enums import QualityVerdict
from app.imaging.quality import assess_quality, blur_score, exposure, glare_fraction
from tests.synthetic import make_card


def test_a_sharp_card_passes():
    report = assess_quality(make_card(px_per_mm=20.0), px_per_mm=20.0)
    assert report.verdict is QualityVerdict.OK
    assert report.reasons == []


def test_blur_is_detected():
    """A soft photo invents scuffing and hides scratches, so it must not pass silently."""
    card = make_card(px_per_mm=20.0)
    blurred = cv2.GaussianBlur(card, (21, 21), 8)

    assert blur_score(cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)) < blur_score(
        cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    )

    report = assess_quality(blurred, px_per_mm=20.0)
    assert report.verdict is QualityVerdict.REVIEW
    assert any("focus" in reason for reason in report.reasons)


def test_resolution_below_the_measurable_limit_is_rejected():
    """Below ~8 px/mm a 2.5mm² defect is under 13px, so grading would be fiction."""
    report = assess_quality(make_card(px_per_mm=20.0), px_per_mm=3.0)
    assert report.verdict is QualityVerdict.REJECT
    assert any("resolution" in reason for reason in report.reasons)


def test_reject_outranks_review():
    """A capture that is both soft and too small is rejected, not merely reviewed."""
    blurred = cv2.GaussianBlur(make_card(px_per_mm=20.0), (21, 21), 8)
    report = assess_quality(blurred, px_per_mm=2.0)
    assert report.verdict is QualityVerdict.REJECT
    assert len(report.reasons) >= 2


def test_blown_highlights_are_flagged():
    card = make_card(px_per_mm=20.0)
    card[: card.shape[0] // 3, :] = 255
    report = assess_quality(card, px_per_mm=20.0)
    assert report.verdict is QualityVerdict.REVIEW
    assert any("blown out" in reason for reason in report.reasons)


def test_crushed_shadows_are_flagged():
    card = make_card(px_per_mm=20.0)
    card[: card.shape[0] // 3, :] = 0
    report = assess_quality(card, px_per_mm=20.0)
    assert report.verdict is QualityVerdict.REVIEW
    assert any("crushed" in reason for reason in report.reasons)


def test_exposure_fractions():
    image = np.zeros((100, 100), dtype=np.uint8)
    image[:10, :] = 255  # 10% blown
    image[90:, :] = 0    # already zero
    clipped, crushed = exposure(image)
    assert clipped == pytest.approx(0.10)
    assert crushed == pytest.approx(0.90)


def test_glare_needs_a_blob_not_scattered_bright_pixels():
    """Artwork highlights are scattered; a reflection is a connected region."""
    rng = np.random.default_rng(3)
    speckled = np.full((400, 300), 120, dtype=np.uint8)
    ys = rng.integers(0, 400, 400)
    xs = rng.integers(0, 300, 400)
    speckled[ys, xs] = 255
    assert glare_fraction(speckled) < 0.01

    blob = np.full((400, 300), 120, dtype=np.uint8)
    cv2.circle(blob, (150, 200), 60, 255, -1)
    assert glare_fraction(blob) > 0.05


def test_glare_is_flagged_on_a_card():
    card = make_card(px_per_mm=20.0)
    cv2.circle(card, (card.shape[1] // 2, card.shape[0] // 2), card.shape[1] // 4,
               (255, 255, 255), -1)
    report = assess_quality(card, px_per_mm=20.0)
    assert any("glare" in reason for reason in report.reasons)


def test_report_carries_the_numbers_not_just_the_verdict():
    """Thresholds get retuned against real captures, which needs the measurements kept."""
    payload = assess_quality(make_card(px_per_mm=20.0), px_per_mm=20.0).as_dict()
    assert set(payload) == {
        "verdict", "blur_score", "clipped_highlights", "crushed_shadows",
        "glare_fraction", "px_per_mm", "margin_px", "area_fraction", "reasons",
    }
    assert payload["px_per_mm"] == 20.0


def test_a_card_touching_the_frame_edge_is_flagged():
    """"Fill the frame" is good advice right up until the card is cropped by it. Then there is
    no visible border on that side, so no detector can crop exactly — the operator has to know."""
    from app.imaging.quality import frame_margin

    card = make_card(px_per_mm=20.0)
    corners = np.array([[2, 3], [1200, 3], [1200, 1700], [2, 1700]], dtype=np.float32)
    assert frame_margin(corners, 1204, 1704) == pytest.approx(2.0)

    report = assess_quality(card, px_per_mm=20.0, margin_px=2.0)
    assert report.verdict is QualityVerdict.REVIEW
    assert any("cropped" in reason for reason in report.reasons)


def test_a_comfortable_margin_is_not_flagged():
    from app.imaging.quality import frame_margin

    corners = np.array([[200, 200], [900, 200], [900, 1180], [200, 1180]], dtype=np.float32)
    assert frame_margin(corners, 1100, 1400) == pytest.approx(200.0)

    report = assess_quality(make_card(px_per_mm=20.0), px_per_mm=20.0, margin_px=200.0)
    assert report.verdict is QualityVerdict.OK


def test_a_tiny_detection_is_flagged_rather_than_passing_as_ok():
    """Regression: a Pokémon card back contains a large pokéball whose bounding quad is very
    nearly card-shaped. Detected instead of the card it scored 0.79 and passed as "ok" — a
    badly wrong crop that looked entirely fine on its own numbers."""
    report = assess_quality(make_card(px_per_mm=20.0), px_per_mm=20.0, area_fraction=0.149)
    assert report.verdict is QualityVerdict.REVIEW
    assert any("only 15% of the frame" in reason for reason in report.reasons)


def test_a_well_framed_card_is_not_flagged_on_area():
    report = assess_quality(make_card(px_per_mm=20.0), px_per_mm=20.0, area_fraction=0.54)
    assert report.verdict is QualityVerdict.OK
