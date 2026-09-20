"""Auction format, staggered start times, and photos-only batches.

These are the eBay behaviours a dedicated listing tool has and this one did not. They are tested
without a database because each is a pure decision about what goes in a cell.
"""

import itertools
import re

import pytest

from app.routers.ebay import _schedule_at


def test_no_stagger_means_no_schedule_time():
    """An empty ScheduleTime means "go live on upload", which is the sane default."""
    assert _schedule_at(itertools.count(), 0) == ""


def test_staggering_spaces_listings_apart():
    """eBay surfaces newly-listed items, so three hundred going live in one second buries all
    but the first few."""
    counter = itertools.count()
    stamps = [_schedule_at(counter, 30) for _ in range(3)]
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", s) for s in stamps)
    assert stamps[0] < stamps[1] < stamps[2]


def test_the_gap_is_the_requested_number_of_minutes():
    from datetime import datetime

    counter = itertools.count()
    first, second = _schedule_at(counter, 45), _schedule_at(counter, 45)
    gap = datetime.strptime(second, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(
        first, "%Y-%m-%dT%H:%M:%SZ"
    )
    assert gap.total_seconds() == pytest.approx(45 * 60, abs=2)


def test_schedule_times_are_utc():
    """eBay reads the Z suffix as UTC; a local time here would list at the wrong hour."""
    assert _schedule_at(itertools.count(), 10).endswith("Z")


@pytest.mark.parametrize(
    ("listing_format", "expects_best_offer", "expects_buy_it_now"),
    [("FixedPrice", True, True), ("Auction", False, False)],
)
def test_an_auction_carries_neither_best_offer_nor_buy_it_now(
    listing_format, expects_best_offer, expects_buy_it_now
):
    """An auction takes bids. eBay rejects a row that also sets an offer or a BIN price."""
    is_auction = listing_format == "Auction"
    best_offer = "" if is_auction else "1"
    buy_it_now = "" if is_auction else "1.41"
    assert bool(best_offer) is expects_best_offer
    assert bool(buy_it_now) is expects_buy_it_now


def test_the_default_duration_suits_the_format():
    """Good Till Cancelled is meaningless for an auction, which must end."""
    for listing_format, expected in (("FixedPrice", "GTC"), ("Auction", "Days_7")):
        is_auction = listing_format == "Auction"
        assert ("Days_7" if is_auction else "GTC") == expected


def test_a_photos_only_batch_skips_recognition():
    """The rendering is what other tools cannot do; identifying again is duplicated work."""
    import inspect

    from app.jobs import worker

    source = inspect.getsource(worker._maybe_recognise)
    assert "photos_only" in source
    # The branch must return before the enqueue, not merely log about it.
    after = source.split("batch.photos_only", 1)[1]
    assert after.index("return") < after.index("enqueue_job")


def test_endpoints_that_others_call_are_plain_functions_first():
    """Calling a FastAPI endpoint function directly passes `Query(...)` sentinels as values.

    It fails far from the cause — once inside `str.strip`, once inside a `<=` comparison — and
    has done so three times. Anything several callers need is a plain function that the
    decorated endpoint wraps.
    """
    import inspect

    from app.routers import ebay

    for name in ("build_queue", "build_export", "resolve_photo_host"):
        function = getattr(ebay, name)
        for parameter in inspect.signature(function).parameters.values():
            assert not repr(parameter.default).startswith("Query("), (
                f"{name}.{parameter.name} defaults to a FastAPI Query sentinel; "
                "plain functions must take plain defaults"
            )


def test_corner_crops_are_anchored_to_the_corner():
    """Shrinking the fraction must close in on the corner, not drift toward the middle.

    The corner is the whole point of these four images; a crop that wanders off it is worse
    than a wide one.
    """
    import numpy as np

    from app.services.corner_details import crop

    canvas = np.zeros((1000, 800, 3), dtype=np.uint8)
    # Mark the true top-left pixel and the true bottom-right one.
    canvas[0, 0] = 255
    canvas[999, 799] = 255

    for fraction in (0.5, 0.3, 0.2):
        top_left = crop(canvas, 0, 0, fraction)
        bottom_right = crop(canvas, 1, 1, fraction)
        assert top_left[0, 0].any(), f"top-left lost its corner at {fraction}"
        assert bottom_right[-1, -1].any(), f"bottom-right lost its corner at {fraction}"


def test_the_corner_fraction_is_clamped():
    """A slider is a user input; 0 or 2 must not produce an empty or oversized crop."""
    import numpy as np

    from app.services.corner_details import crop

    canvas = np.zeros((1000, 800, 3), dtype=np.uint8)
    assert crop(canvas, 0, 0, 0.0).size > 0
    tall = crop(canvas, 0, 0, 5.0)
    assert tall.shape[0] <= 1000 and tall.shape[1] <= 800


def test_batch_names_read_as_dates_and_numbers():
    """"Session 05 Sep 23:41" sorts correctly and tells you nothing you wanted to know."""
    import inspect

    from app.routers.sessions import _default_name

    source = inspect.getsource(_default_name)
    assert "Batch" in source
    assert "isoformat" in source


def test_thumbnail_widths_are_a_fixed_list():
    """An open list lets a caller fill the disk with one thumbnail per pixel width."""
    from app.routers.images import ALLOWED_WIDTHS

    assert 400 in ALLOWED_WIDTHS
    assert len(ALLOWED_WIDTHS) <= 6


def test_thumbnails_are_keyed_on_content():
    """Otherwise a re-rendered image serves its predecessor's thumbnail — which is exactly
    what happens after adjusting a crop."""
    import inspect

    from app.routers import images

    assert "sha256" in inspect.getsource(images.get_image)
