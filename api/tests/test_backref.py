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


def test_registration_ships_descriptors_not_artwork():
    """The reference photograph is a picture of a Pokémon card back, which belongs to Nintendo
    / Creatures / GAME FREAK. A distributable build must not contain it.

    Registration only ever needs the reference's ORB features and its dimensions — the pixels
    are used for `shape` and nothing else — so the features are baked to disk and the
    photograph is excluded from built images. Verified bit-identical against six real card
    backs: same inlier counts, zero corner delta.
    """
    import inspect

    from app.imaging import backref

    # The descriptors exist and are what gets loaded first.
    assert backref.DESCRIPTORS_PATH.exists(), "run `make bake-reference`"
    source = inspect.getsource(backref._reference)
    assert source.index("DESCRIPTORS_PATH") < source.index("REFERENCE_PATH")

    # That the photograph is actually absent from a built image is checked against the image
    # itself by `scripts/release-check.sh` — `.dockerignore` governs the build context and is
    # never inside the result, so it cannot be asserted from in here.


def test_the_baked_reference_is_not_the_image():
    """A set of binary ORB descriptors encodes "there is a corner here, at this scale, with
    this gradient pattern". It is not a reproduction and cannot be turned back into one."""
    import numpy as np

    from app.imaging import backref

    with np.load(backref.DESCRIPTORS_PATH) as data:
        assert set(data.files) == {
            "points",
            "sizes",
            "angles",
            "responses",
            "octaves",
            "descriptors",
            "shape",
        }
        # Descriptors are 32 bytes of binary comparisons per keypoint, not pixels.
        assert data["descriptors"].dtype == np.uint8
        assert data["descriptors"].shape[1] == 32
        assert len(data["points"]) == len(data["descriptors"])


def test_registration_works_with_the_photograph_absent():
    """The property a distributed build depends on: no artwork on disk, registration
    unchanged. Simulated by pointing the photograph at a path that does not exist, which is
    exactly what a built image looks like."""
    import glob
    from pathlib import Path

    import cv2

    from app.imaging import backref

    backs = sorted(glob.glob("/data/images/*/original-back.jpg"))
    if not backs:
        import pytest

        pytest.skip("no captured card backs on this machine")

    original = backref.REFERENCE_PATH
    try:
        backref.REFERENCE_PATH = Path("/nonexistent/pokemon-back.jpg")
        backref._reference.cache_clear()
        assert backref._reference() is not None, "descriptors did not load without the image"

        registration = backref.register_back(cv2.imread(backs[0]))
        assert registration is not None
        assert registration.inliers >= backref.MIN_INLIERS
    finally:
        backref.REFERENCE_PATH = original
        backref._reference.cache_clear()
