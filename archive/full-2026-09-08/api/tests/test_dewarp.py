"""Rectification: does a warped card come back as the card it started as?"""

import cv2
import numpy as np
import pytest

from app.imaging.detect import detect_card
from app.imaging.dewarp import dewarp, effective_px_per_mm
from app.imaging.geometry import CARD_HEIGHT_MM, order_corners, output_size
from tests.synthetic import card_quad, make_card, place_on_background


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Normalised cross-correlation of two images at a common size, 0..1."""
    target = (300, 420)
    ga = cv2.cvtColor(cv2.resize(a, target), cv2.COLOR_BGR2GRAY).astype(np.float64)
    gb = cv2.cvtColor(cv2.resize(b, target), cv2.COLOR_BGR2GRAY).astype(np.float64)
    ga -= ga.mean()
    gb -= gb.mean()
    denom = np.sqrt((ga**2).sum() * (gb**2).sum())
    return float((ga * gb).sum() / denom) if denom else 0.0


def test_output_is_exactly_the_requested_physical_size():
    scene, corners = place_on_background(make_card())
    result = dewarp(scene, corners, px_per_mm=20.0)

    assert (result.width, result.height) == output_size(20.0) == (1260, 1760)
    assert result.px_per_mm == 20.0
    assert result.rotation_applied == 0


@pytest.mark.parametrize("scale", [12.0, 20.0, 40.0, 80.0])
def test_pixels_convert_to_millimetres_at_any_scale(scale):
    """The rectification scale is chosen per capture, so nothing may assume a constant. What
    must hold at every scale is that a pixel count divides cleanly into millimetres."""
    from app.imaging.geometry import mm2_from_px2, mm_from_px

    scene, corners = place_on_background(make_card())
    result = dewarp(scene, corners, px_per_mm=scale)

    assert mm_from_px(result.height, result.px_per_mm) == pytest.approx(CARD_HEIGHT_MM)
    assert mm_from_px(result.width, result.px_per_mm) == pytest.approx(63.0, rel=0.01)
    # A 2.5mm² defect, the smallest threshold in the rubric, in pixels at this scale.
    assert mm2_from_px2(2.5 * scale * scale, scale) == pytest.approx(2.5)


def test_a_warped_card_is_recovered():
    """The round trip that the whole pipeline rests on."""
    card = make_card()
    scene, corners = place_on_background(card)
    result = dewarp(scene, corners, px_per_mm=20.0)

    assert similarity(result.image, card) > 0.95


def test_recovery_holds_through_detection_rather_than_ground_truth():
    """Same round trip, but using the corners the detector actually found."""
    card = make_card()
    scene, _ = place_on_background(card)
    detection = detect_card(scene)
    assert detection is not None

    result = dewarp(scene, detection.corners, px_per_mm=20.0)
    assert similarity(result.image, card) > 0.94


def test_a_sideways_card_comes_out_portrait():
    card = make_card()
    sideways = cv2.rotate(card, cv2.ROTATE_90_CLOCKWISE)
    corners = card_quad((800, 600), height_px=1080, landscape=True)
    scene, truth = place_on_background(sideways, corners=corners)

    result = dewarp(scene, truth, px_per_mm=20.0)

    assert (result.width, result.height) == (1260, 1760), "must be portrait, not squashed"
    assert result.rotation_applied == 90
    assert similarity(result.image, card) > 0.9


@pytest.mark.parametrize("scale", [10.0, 20.0, 30.0])
def test_scale_is_honoured_exactly(scale):
    scene, corners = place_on_background(make_card())
    result = dewarp(scene, corners, px_per_mm=scale)
    assert (result.width, result.height) == output_size(scale)
    # The property condition measurement depends on: pixels convert to mm by division.
    assert result.height / CARD_HEIGHT_MM == pytest.approx(scale, rel=0.002)


def test_dewarp_does_not_alter_colour():
    """Geometry only. Auto-levels here would erase edge whitening (docs/IMAGING.md)."""
    card = make_card()
    # Warp onto an axis-aligned quad the same size as the card, so the transform is a
    # near-identity resize and any colour change would be the dewarp's own doing.
    corners = np.array(
        [[0, 0], [card.shape[1] - 1, 0],
         [card.shape[1] - 1, card.shape[0] - 1], [0, card.shape[0] - 1]],
        dtype=np.float32,
    )
    result = dewarp(card, corners, px_per_mm=card.shape[0] / CARD_HEIGHT_MM)

    resized = cv2.resize(card, (result.width, result.height), interpolation=cv2.INTER_CUBIC)
    assert abs(float(result.image.mean()) - float(resized.mean())) < 1.0
    for channel in range(3):
        assert abs(
            float(result.image[:, :, channel].mean()) - float(resized[:, :, channel].mean())
        ) < 1.5


def test_effective_scale_reports_the_source_resolution():
    """Not the output scale — upscaling a small crop invents no detail."""
    scene, corners = place_on_background(make_card(), scene_size=(1600, 1200))
    # The synthetic card is 86% of a 1200px-tall frame, so about 1032px over 88mm.
    assert effective_px_per_mm(corners) == pytest.approx(1032 / 88.0, rel=0.1)


def test_effective_scale_is_low_for_a_small_capture():
    corners = card_quad((400, 300), height_px=176)  # 176px over 88mm = 2 px/mm
    assert effective_px_per_mm(corners) == pytest.approx(2.0, rel=0.05)


def test_dewarp_orders_corners_itself():
    """Regression: passing an unordered quad used to warp to silently wrong geometry."""
    card = make_card()
    scene, corners = place_on_background(card)

    ordered = dewarp(scene, corners, px_per_mm=20.0)
    shuffled = dewarp(scene, np.roll(corners, 2, axis=0), px_per_mm=20.0)

    assert similarity(ordered.image, shuffled.image) > 0.99


def test_the_card_border_survives_the_crop():
    """Edgewear lives in the outermost millimetre, so a crop that shaves the border would
    silently erase the most common defect there is.

    The fit targets the card/background intensity step, so the boundary is included by
    construction — this asserts it rather than assuming it. The outer band of a rectified card
    must be the card's own border, not the background it was photographed on.
    """
    card = make_card(px_per_mm=16.0)
    scene, _ = place_on_background(card, background=(20, 20, 20), noise=0)

    detection = detect_card(scene)
    assert detection is not None
    result = dewarp(scene, detection.corners, px_per_mm=20.0, contour=detection.contour)

    # The synthetic card has a yellow border; the background is near-black. Sample a one-pixel
    # band inset slightly from each edge, away from the rounded-corner regions.
    image = result.image
    inset = 2
    trim = int(result.height * 0.1)
    bands = [
        image[inset, trim:-trim],            # top
        image[-1 - inset, trim:-trim],       # bottom
        image[trim:-trim, inset],            # left
        image[trim:-trim, -1 - inset],       # right
    ]
    for index, band in enumerate(bands):
        mean = band.reshape(-1, 3).mean(axis=0)
        assert mean.max() > 90, (
            f"edge {index} is background, not card border — the crop shaved the card"
        )


def test_the_crop_does_not_include_a_wide_background_margin():
    """The complement: a crop loose enough to bring in background would inflate every area
    measurement and put phantom whitening along the border."""
    card = make_card(px_per_mm=16.0)
    scene, _ = place_on_background(card, background=(20, 20, 20), noise=0)

    detection = detect_card(scene)
    assert detection is not None
    result = dewarp(scene, detection.corners, px_per_mm=20.0, contour=detection.contour)

    # 1% of the card's width either side would be 0.6mm of phantom card.
    band = int(result.width * 0.01)
    trim = int(result.height * 0.1)
    left = result.image[trim:-trim, :band].reshape(-1, 3).mean(axis=0)
    right = result.image[trim:-trim, -band:].reshape(-1, 3).mean(axis=0)
    assert left.max() > 90 and right.max() > 90, "background bled into the crop"


def test_listing_render_puts_an_equal_margin_on_every_side():
    """A crop cut hard to the card's edge gives a buyer nowhere to look: edge whitening and
    corner wear sit at the boundary, and with no background behind them there is no reference
    for where the card ends. The margin has to be equal on all four sides — a scale factor
    would leave more of it along the long edge."""
    from app.imaging.geometry import CARD_HEIGHT_MM, CARD_WIDTH_MM
    from app.services.listing_images import render

    card = make_card(px_per_mm=16.0)
    scene, corners = place_on_background(card, background=(20, 20, 20), noise=0)

    px_per_mm, margin_mm = 20.0, 5.0
    canvas = render(scene, corners, px_per_mm, margin_mm)

    expected_w = round(CARD_WIDTH_MM * px_per_mm + 2 * margin_mm * px_per_mm)
    expected_h = round(CARD_HEIGHT_MM * px_per_mm + 2 * margin_mm * px_per_mm)
    assert (canvas.shape[1], canvas.shape[0]) == (expected_w, expected_h)

    # Each margin band should be background, not card.
    band = int(margin_mm * px_per_mm) - 3
    for strip in (
        canvas[:band, :],
        canvas[-band:, :],
        canvas[:, :band],
        canvas[:, -band:],
    ):
        assert strip.reshape(-1, 3).mean() < 80, "margin should show background, not card"

    # And the centre should be card, not background.
    centre = canvas[canvas.shape[0] // 2, canvas.shape[1] // 2]
    assert centre.max() > 80

def test_oriented_maps_the_first_corner_to_the_output_top_left():
    """The contract that makes orientation correction work.

    Registration returns the reference's corners projected into the photograph, so the *first*
    is the card's own top-left whichever way up the card was lying. `oriented=True` must map
    that corner to the output's top-left; the default orders by image position instead and
    would rectify an upside-down card upside-down.

    Tested geometrically rather than through ORB: ORB descriptors are rotation invariant, so
    telling 0 from 180 needs genuinely asymmetric artwork. Real cards have it — a photographed
    Crobat rectifies correctly from all four right-angle rotations — but the synthetic card
    does not, which would make an ORB-driven test measure the fixture rather than the code.
    """
    card = make_card(px_per_mm=16.0)
    scene, corners = place_on_background(card, background=(20, 20, 20), noise=0)
    ordered = order_corners(corners)

    # Hand the corners over rotated by two positions: the card's "top-left" is now what sits at
    # the bottom-right of the frame, exactly as it would be for a card photographed upside-down.
    flipped = np.roll(ordered, 2, axis=0)

    upright = dewarp(scene, ordered, px_per_mm=20.0, oriented=True).image
    inverted = dewarp(scene, flipped, px_per_mm=20.0, oriented=True).image

    # Honouring the given order must produce a 180-degree different image...
    assert similarity(inverted, cv2.rotate(upright, cv2.ROTATE_180)) > 0.95

    # ...while ordering by image position ignores it and reproduces the upright one.
    reordered = dewarp(scene, flipped, px_per_mm=20.0, oriented=False).image
    assert similarity(reordered, upright) > 0.95


def test_a_sideways_quad_is_not_rotated_twice_when_orientation_is_given():
    """Regression: with `oriented=True` the landscape check must not fire. The reference order
    already rotates the content by itself, so testing the quad's shape as well rotated a second
    time and turned a card photographed sideways into a sideways crop."""
    card = make_card(px_per_mm=16.0)
    sideways = cv2.rotate(card, cv2.ROTATE_90_CLOCKWISE)
    corners = card_quad((800, 600), height_px=1000, landscape=True)
    scene, truth = place_on_background(sideways, corners=corners, background=(20, 20, 20), noise=0)

    result = dewarp(scene, order_corners(truth), px_per_mm=20.0, oriented=True)

    assert (result.width, result.height) == (1260, 1760), "must still be portrait"
    assert result.rotation_applied == 0, "oriented callers rotate via the corner order alone"


def test_listing_render_is_the_same_size_for_every_card():
    """Uniformity is the product requirement, not a nicety.

    Rendering at each capture's own scale produced images from 2336 to 2774 px wide across one
    batch — and a card's own front and back differing from each other — because px/mm tracks
    how close the card was shot. In a marketplace gallery that reads as carelessness. A fixed
    render scale makes every listing image identical in size whatever the capture distance.
    """
    from app.imaging.geometry import CARD_HEIGHT_MM, CARD_WIDTH_MM
    from app.services.listing_images import render

    margin_mm, px_per_mm = 5.0, 30.0
    expected = (
        round((CARD_WIDTH_MM + 2 * margin_mm) * px_per_mm),
        round((CARD_HEIGHT_MM + 2 * margin_mm) * px_per_mm),
    )

    # Two cards shot at very different distances must still render identically.
    for height_px in (620, 1050):
        card = make_card(px_per_mm=16.0)
        corners = card_quad((800, 600), height_px, 2.0, 0.0)
        scene, _ = place_on_background(card, corners=corners, background=(20, 20, 20), noise=0)
        canvas = render(scene, corners, px_per_mm, margin_mm)
        assert (canvas.shape[1], canvas.shape[0]) == expected


def test_listing_margin_is_adjustable_and_uniform():
    """The spacing is a setting, applied identically to every card."""
    from app.imaging.geometry import CARD_WIDTH_MM
    from app.services.listing_images import render

    card = make_card(px_per_mm=16.0)
    scene, corners = place_on_background(card, background=(20, 20, 20), noise=0)

    px_per_mm = 30.0
    widths = {}
    for margin_mm in (0.0, 5.0, 9.0):
        canvas = render(scene, corners, px_per_mm, margin_mm)
        widths[margin_mm] = canvas.shape[1]
        assert canvas.shape[1] == round((CARD_WIDTH_MM + 2 * margin_mm) * px_per_mm)

    assert widths[0.0] < widths[5.0] < widths[9.0]
