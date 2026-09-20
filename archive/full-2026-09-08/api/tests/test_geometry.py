"""Card geometry and corner ordering."""

import numpy as np
import pytest

from app.imaging.geometry import (
    CARD_ASPECT,
    CARD_HEIGHT_MM,
    CARD_WIDTH_MM,
    is_convex,
    order_corners,
    output_size,
    quad_area,
    quad_aspect,
    side_lengths,
)


def test_card_dimensions_match_the_grading_rubric():
    """One definition of card size, or the dewarp desynchronises from the mm thresholds."""
    from app.conditioning.rubric import CARD_AREA_MM2, CARD_LENGTH_MM

    assert CARD_HEIGHT_MM == CARD_LENGTH_MM == 88.0
    assert CARD_WIDTH_MM == 63.0
    assert CARD_WIDTH_MM * CARD_HEIGHT_MM == CARD_AREA_MM2


def test_output_size_at_the_default_scale():
    assert output_size(20.0) == (1260, 1760)


def test_output_size_scales_linearly():
    assert output_size(10.0) == (630, 880)
    assert output_size(40.0) == (2520, 3520)


def test_output_size_rejects_nonsense():
    with pytest.raises(ValueError):
        output_size(0)


def test_one_pixel_is_one_twentieth_of_a_millimetre_at_the_default():
    """The property the whole condition engine depends on (docs/IMAGING.md)."""
    width, height = output_size(20.0)
    assert width / CARD_WIDTH_MM == 20.0
    assert height / CARD_HEIGHT_MM == 20.0


@pytest.mark.parametrize("rotation", [0, 1, 2, 3])
def test_corner_ordering_is_independent_of_input_order(rotation):
    canonical = np.array([[10, 20], [110, 25], [105, 160], [8, 155]], dtype=np.float32)
    shuffled = np.roll(canonical, rotation, axis=0)
    np.testing.assert_allclose(order_corners(shuffled), canonical, atol=1e-4)


def test_corner_ordering_handles_reversed_winding():
    canonical = np.array([[10, 20], [110, 25], [105, 160], [8, 155]], dtype=np.float32)
    np.testing.assert_allclose(order_corners(canonical[::-1]), canonical, atol=1e-4)


def test_degenerate_quad_is_rejected():
    with pytest.raises(ValueError):
        order_corners(np.array([[0, 0], [0, 0], [10, 10], [10, 0]], dtype=np.float32))


def test_quad_area_of_a_rectangle():
    square = np.array([[0, 0], [100, 0], [100, 50], [0, 50]], dtype=np.float32)
    assert quad_area(square) == pytest.approx(5000.0)


def test_quad_aspect_recognises_a_card_shape():
    card = np.array([[0, 0], [630, 0], [630, 880], [0, 880]], dtype=np.float32)
    assert quad_aspect(card) == pytest.approx(CARD_ASPECT, rel=0.01)


def test_quad_aspect_of_a_sideways_card_is_the_reciprocal():
    sideways = np.array([[0, 0], [880, 0], [880, 630], [0, 630]], dtype=np.float32)
    assert quad_aspect(sideways) == pytest.approx(1 / CARD_ASPECT, rel=0.01)


def test_side_lengths():
    rect = np.array([[0, 0], [100, 0], [100, 50], [0, 50]], dtype=np.float32)
    top, right, bottom, left = side_lengths(rect)
    assert (top, bottom) == (100.0, 100.0)
    assert (right, left) == (50.0, 50.0)


def test_convexity():
    convex = np.array([[0, 0], [100, 0], [100, 50], [0, 50]], dtype=np.float32)
    assert is_convex(convex)
    bowtie = np.array([[0, 0], [100, 0], [0, 50], [100, 50]], dtype=np.float32)
    assert not is_convex(bowtie)
