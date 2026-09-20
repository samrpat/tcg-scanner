"""Back-reference registration geometry."""

import numpy as np

from app.imaging.backref import _rotate_about_centre


def test_rotate_about_centre_preserves_centre_and_size():
    """The correction must only rotate: same centre, same side lengths."""
    quad = np.float32([[0, 0], [629, 0], [629, 879], [0, 879]])
    rotated = _rotate_about_centre(quad, 0.64)

    assert np.allclose(rotated.mean(axis=0), quad.mean(axis=0), atol=1e-3)
    for i in range(4):
        before = np.linalg.norm(quad[(i + 1) % 4] - quad[i])
        after = np.linalg.norm(rotated[(i + 1) % 4] - rotated[i])
        assert abs(before - after) < 1e-2


def test_rotate_about_centre_applies_the_expected_angle():
    quad = np.float32([[0, 0], [100, 0], [100, 100], [0, 100]])
    rotated = _rotate_about_centre(quad, 90.0)

    # The top edge should now run vertically.
    edge = rotated[1] - rotated[0]
    assert abs(np.degrees(np.arctan2(edge[1], edge[0])) - 90.0) < 1e-3


def test_rotate_about_centre_is_a_noop_at_zero():
    quad = np.float32([[0, 0], [10, 0], [10, 20], [0, 20]])
    assert np.array_equal(_rotate_about_centre(quad, 0.0), quad)
