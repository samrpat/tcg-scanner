"""Reading the collector number off a rectified card."""

import numpy as np

from app.imaging.ocr import (
    CARD_W_MM,
    REGION_BOTTOM_LEFT,
    REGION_BOTTOM_RIGHT,
    _crop_mm,
    _parse,
    read_collector_number,
)


def test_regions_sit_inside_the_card():
    for region in (REGION_BOTTOM_RIGHT, REGION_BOTTOM_LEFT):
        x0, y0, x1, y1 = region
        assert 0 <= x0 < x1 <= 63.0
        assert 0 <= y0 < y1 <= 88.0


def test_crop_is_taken_in_millimetres_not_pixels():
    """The whole point of rectifying to 88x63 mm: the number is at a known physical place."""
    image = np.zeros((880, 630, 3), np.uint8)
    px_per_mm = image.shape[1] / CARD_W_MM
    patch = _crop_mm(image, REGION_BOTTOM_RIGHT, px_per_mm)

    x0, y0, x1, y1 = REGION_BOTTOM_RIGHT
    assert patch.shape[1] == round(x1 * px_per_mm) - round(x0 * px_per_mm)
    assert patch.shape[0] == round(y1 * px_per_mm) - round(y0 * px_per_mm)


def test_parses_a_number_and_total():
    parsed = _parse("35/113", "bottom_right", 0.9)
    assert (parsed.number, parsed.total) == (35, 113)


def test_parses_a_zero_padded_number():
    parsed = _parse("005/086", "bottom_left", 0.9)
    assert (parsed.number, parsed.total) == (5, 86)


def test_accepts_a_secret_rare_above_the_set_total():
    parsed = _parse("102/086", "bottom_left", 0.9)
    assert (parsed.number, parsed.total) == (102, 86)


def test_rejects_an_impossible_pair():
    assert _parse("250/100", "bottom_right", 0.9) is None


def test_parses_a_promo_without_a_total():
    parsed = _parse("XY48", "bottom_right", 0.9)
    assert parsed.number == 48
    assert parsed.prefix == "XY"
    assert parsed.total is None


def test_does_not_mistake_neighbouring_words_for_a_promo_prefix():
    """"Illus." and the "CRI EN" boxes are printed right beside the number."""
    assert _parse("ILLUS 48", "bottom_right", 0.9) is None or _parse(
        "ILLUS 48", "bottom_right", 0.9
    ).prefix != "ILLUS"


def test_a_bare_number_is_worth_less_than_a_checked_pair():
    bare = _parse("086", "bottom_left", 1.0)
    pair = _parse("86/086", "bottom_left", 1.0)
    assert bare.confidence < pair.confidence


def test_a_blank_card_reads_nothing_rather_than_guessing():
    assert read_collector_number(np.full((880, 630, 3), 200, np.uint8)) is None


def test_an_empty_image_is_handled():
    assert read_collector_number(np.zeros((0, 0, 3), np.uint8)) is None
