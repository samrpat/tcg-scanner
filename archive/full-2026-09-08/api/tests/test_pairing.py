"""Front/back pairing rules. Pure logic — the database side is exercised by the API tests."""

import pytest

from app.services.capture import pair_batch, side_from_filename


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("CARD-000001-front.jpg", "front"),
        ("CARD-000001-back.jpg", "back"),
        ("scan_f.png", "front"),
        ("scan_b.png", "back"),
        ("IMG_0042 front.jpeg", "front"),
        ("reverse-side.jpg", "back"),
        ("obverse.jpg", "front"),
        ("photos/nested/thing-back.jpg", "back"),
    ],
)
def test_filename_hints_are_read(filename, expected):
    assert side_from_filename(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "IMG_0042.jpg",       # no hint
        "front-and-back.jpg",  # both, so genuinely ambiguous
        "",
        None,
    ],
)
def test_ambiguous_filenames_return_nothing(filename):
    """Guessing wrong here silently mislabels which side of a card is which."""
    assert side_from_filename(filename) is None


def test_a_word_containing_a_hint_is_not_a_hint():
    """`f` and `b` are hints as words, not as substrings — "buffalo" is not a back."""
    assert side_from_filename("buffalo.jpg") is None
    assert side_from_filename("effort.jpg") is None


def test_batch_uses_filename_hints_when_every_file_has_one():
    names = ["a-front.jpg", "a-back.jpg", "b-front.jpg", "b-back.jpg"]
    assert pair_batch(names) == ["front", "back", "front", "back"]


def test_batch_respects_hints_even_out_of_order():
    names = ["a-back.jpg", "a-front.jpg"]
    assert pair_batch(names) == ["back", "front"]


def test_batch_falls_back_to_arrival_order_without_hints():
    names = ["IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg", "IMG_4.jpg"]
    assert pair_batch(names) == ["front", "back", "front", "back"]


def test_partial_hints_fall_back_rather_than_half_trusting_names():
    """Trusting two of four names would interleave the pairs wrongly for the rest."""
    names = ["a-front.jpg", "IMG_2.jpg", "b-front.jpg", "IMG_4.jpg"]
    assert pair_batch(names) == ["front", "back", "front", "back"]


def test_an_odd_batch_leaves_a_visible_half_pair():
    """Better a card obviously missing its back than a silently dropped image."""
    assert pair_batch(["a.jpg", "b.jpg", "c.jpg"]) == ["front", "back", "front"]


def test_empty_batch():
    assert pair_batch([]) == []
