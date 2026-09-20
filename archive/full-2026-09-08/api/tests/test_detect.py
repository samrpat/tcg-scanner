"""Card detection against synthetic scenes with known ground truth."""

import cv2
import numpy as np
import pytest

from app.imaging.detect import detect_card
from tests.synthetic import card_quad, corner_error, make_card, place_on_background


def test_detects_a_card_and_recovers_its_corners():
    scene, truth = place_on_background(make_card())
    detection = detect_card(scene)

    assert detection is not None
    # Sub-pixel refinement puts corners within a couple of pixels of truth, which matters:
    # at 20 px/mm the rectified image, 2px is 0.1mm on the edgewear measurement.
    assert corner_error(detection.corners, truth) < 3.0
    assert detection.confidence > 0.5
    # Which strategy found it is not the contract — that it was found precisely is.
    assert not detection.method.endswith("min_area_rect")


def test_corners_come_back_ordered():
    scene, truth = place_on_background(make_card())
    detection = detect_card(scene)
    assert detection is not None

    corners = detection.corners
    # top-left is left of top-right, and above bottom-left
    assert corners[0][0] < corners[1][0]
    assert corners[0][1] < corners[3][1]


@pytest.mark.parametrize(
    ("label", "height_px", "rotation", "perspective"),
    [
        ("square on", 1000, 0.0, 0.0),
        ("slightly rotated", 1000, 6.0, 0.0),
        ("shot from an angle", 980, 3.0, 0.06),
        ("rotated the other way", 950, -8.0, 0.02),
        ("smaller in frame", 620, 2.0, 0.0),
    ],
)
def test_detection_survives_realistic_framings(label, height_px, rotation, perspective):
    corners = card_quad((800, 600), height_px, rotation, perspective)
    scene, truth = place_on_background(make_card(), corners=corners)
    detection = detect_card(scene)
    assert detection is not None, label
    assert corner_error(detection.corners, truth) < 8.0, label
    assert detection.confidence > 0.5, label


def test_a_sideways_card_is_still_detected():
    """Landscape framing must not be rejected on aspect ratio alone."""
    # A card-shaped quad turned on its side: 90 degrees of rotation makes it landscape.
    corners = card_quad((800, 600), height_px=1080, landscape=True)
    card = make_card()
    sideways = cv2.rotate(card, cv2.ROTATE_90_CLOCKWISE)
    scene, truth = place_on_background(sideways, corners=corners)

    detection = detect_card(scene)
    assert detection is not None
    assert corner_error(detection.corners, truth) < 8.0
    assert detection.aspect < 1.0  # reported as landscape


def test_corners_are_refined_to_sub_pixel_accuracy():
    """Thresholding biases a boundary outward by several pixels. Unrefined, that is 0.3mm of
    phantom card sitting exactly where edgewear gets measured."""
    scene, truth = place_on_background(make_card())
    detection = detect_card(scene)
    assert detection is not None
    assert corner_error(detection.corners, truth) < 3.0


def test_the_frame_border_is_not_mistaken_for_a_card():
    """A 1600x1200 frame has aspect 0.75, uncomfortably close to a sideways card's 0.716.
    Adaptive thresholding will happily return the image border as a perfect quad."""
    scene, _ = place_on_background(make_card())
    detection = detect_card(scene)
    assert detection is not None
    assert detection.area_fraction < 0.9


def test_an_empty_scene_returns_nothing():
    """Returning None is the correct answer. A confident wrong quad mis-measures every defect."""
    empty = np.full((900, 1200, 3), 30, dtype=np.uint8)
    assert detect_card(empty) is None


def test_pure_noise_does_not_produce_a_confident_card():
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 255, (900, 1200, 3), dtype=np.uint8)
    detection = detect_card(noise)
    assert detection is None or detection.confidence < 0.55


def test_empty_input_is_handled():
    assert detect_card(np.zeros((0, 0, 3), dtype=np.uint8)) is None


def test_a_tiny_card_in_a_large_frame_is_ignored():
    """Below the minimum area fraction it is more likely a sticker or a distant object."""
    corners = card_quad((400, 300), height_px=170)
    scene, _ = place_on_background(
        make_card(), scene_size=(2400, 1800), corners=corners
    )
    detection = detect_card(scene)
    assert detection is None or detection.area_fraction >= 0.04


def test_line_fitting_survives_an_obstructed_edge():
    """A finger across an edge puts a consistent run of samples on a *different* straight line.
    Sigma-clipping fits halfway between the two; RANSAC picks the larger consensus, so the card
    edge wins. Without this the fit residual on an obstructed edge was 3.5px instead of 0.3px."""
    from app.imaging.edges import fit_edge

    card = make_card()
    scene, truth = place_on_background(card)

    # Paint an opaque blob across the middle of the left edge, like a thumb.
    ordered = truth.astype(int)
    top_left, bottom_left = ordered[0], ordered[3]
    midpoint = ((top_left + bottom_left) // 2)
    cv2.circle(scene, tuple(midpoint), 55, (210, 180, 160), -1)

    grey = cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY)
    fit = fit_edge(grey, truth[3], truth[0])

    assert fit is not None
    assert fit.residual_px < 1.0, "the obstruction must be rejected, not fitted through"
    assert fit.trustworthy


def test_line_fitting_reports_a_tight_residual_on_clean_edges():
    from app.imaging.edges import fit_edge

    scene, truth = place_on_background(make_card())
    grey = cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY)
    for index in range(4):
        fit = fit_edge(grey, truth[index], truth[(index + 1) % 4])
        assert fit is not None
        assert fit.residual_px < 0.6, f"edge {index} fitted loosely"


def test_refinement_declines_rather_than_guessing():
    """Given a quad nowhere near a card, the fitter must return nothing so the caller keeps
    the corners it already had."""
    from app.imaging.edges import refine_quad

    blank = np.full((900, 1200), 40, dtype=np.uint8)
    quad = np.array([[100, 100], [500, 100], [500, 660], [100, 660]], dtype=np.float32)
    assert refine_quad(blank, quad) is None


def test_the_card_wins_over_the_mat_it_is_lying_on():
    """Regression: suppression used to prefer the larger of two overlapping quads, on the
    theory that a card contains its artwork window. But a card is also *contained by* the mat
    it lies on, and "bigger wins" then discarded a perfect detection of the card in favour of
    the background — whose aspect, being the frame's own, is close to a sideways card's."""
    scene, truth = place_on_background(
        make_card(), corners=card_quad((800, 600), 900, 2.0, 0.0)
    )
    # A large, plausible dark rectangle behind the card, like a play mat.
    mat = scene.copy()
    cv2.rectangle(mat, (40, 30), (1560, 1170), (52, 54, 58), -1)
    ordered = truth.astype(np.int32)
    cv2.fillConvexPoly(mat, ordered, (0, 0, 0))
    mat[np.all(mat == 0, axis=2)] = 0
    # Paint the card back over the mat.
    card_mask = np.zeros(scene.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(card_mask, ordered, 255)
    mat[card_mask > 0] = scene[card_mask > 0]

    detection = detect_card(mat)
    assert detection is not None
    assert corner_error(detection.corners, truth) < 10.0, "detected the mat, not the card"
    assert detection.area_fraction < 0.75


def test_a_quad_hugging_the_frame_is_penalised():
    """The image border is a perfect rectangle whose aspect is the frame's own. On a 4:3 frame
    that is 0.75, uncomfortably close to a sideways card's 0.716."""
    from app.imaging.detect import _score

    frame = (1200, 1600)
    hugging = np.array([[1, 1], [1598, 1], [1598, 1198], [1, 1198]], dtype=np.float32)
    inset = np.array([[300, 200], [1300, 200], [1300, 950], [300, 950]], dtype=np.float32)

    hugging_score, *_ = _score(hugging, 1_900_000, 1_920_000, "canny", frame)
    inset_score, *_ = _score(inset, 740_000, 1_920_000, "canny", frame)
    assert hugging_score < inset_score


def test_a_circular_graphic_does_not_beat_the_card_that_contains_it():
    """Regression from a real batch: a Pokémon card back has a large pokéball whose bounding
    quad is card-shaped, and it was winning on aspect and fill while the card's own outline —
    present at higher confidence — was discarded by the shape gate before ranking even began.

    The fix is to rank on measured edge support: a card border is four straight intensity
    edges, a circle's bounding box is not.
    """
    card = make_card(px_per_mm=16.0)
    # A big circle in the middle, like the pokéball.
    centre = (card.shape[1] // 2, card.shape[0] // 2)
    cv2.circle(card, centre, int(card.shape[1] * 0.36), (240, 240, 250), -1)
    cv2.circle(card, centre, int(card.shape[1] * 0.36), (30, 30, 30), 6)

    scene, truth = place_on_background(card, corners=card_quad((800, 600), 950, 2.0, 0.0))
    detection = detect_card(scene)

    assert detection is not None
    assert corner_error(detection.corners, truth) < 12.0, "detected the graphic, not the card"
    assert detection.area_fraction > 0.25


def test_edge_support_is_reported_for_diagnosis():
    scene, _ = place_on_background(make_card())
    detection = detect_card(scene)
    assert detection is not None
    assert detection.edge_fit is not None
    assert "support" in detection.edge_fit
    assert 0.0 <= detection.edge_fit["support"] <= 1.0


def test_back_registration_does_not_fire_on_a_card_front():
    """Registration is also the front/back discriminator, so a false positive would rectify a
    front using the back's geometry. Measured 0/10 on real fronts; this guards the principle."""
    from app.imaging.backref import register_back

    scene, _ = place_on_background(make_card(px_per_mm=16.0))
    assert register_back(scene) is None


def test_back_registration_declines_on_a_blank_frame():
    from app.imaging.backref import register_back

    assert register_back(np.full((900, 1200, 3), 30, dtype=np.uint8)) is None
    assert register_back(np.zeros((0, 0, 3), dtype=np.uint8)) is None


def test_the_reference_back_ships_with_the_package():
    """The reference is the whole basis of back detection; a missing asset would silently
    disable it and send every back back to edge detection."""
    from app.imaging.backref import REFERENCE_PATH, _reference

    assert REFERENCE_PATH.exists(), "reference card back is missing from the package"
    loaded = _reference()
    assert loaded is not None
    _, keypoints, descriptors = loaded
    assert len(keypoints) > 200
    assert descriptors is not None
