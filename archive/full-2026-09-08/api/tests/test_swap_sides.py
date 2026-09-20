"""Swapping a card's front and back.

A card photographed back-first simply fails to identify and gives no reason, because recognition
only ever looks at the front. The fix used to be deleting the card and shooting it again, which
throws away two good photographs for arriving in the wrong order.
"""

import inspect

from app.enums import ImageKind
from app.services import swap_sides


def test_everything_derived_from_the_originals_is_discarded():
    """The processed crops, listing renders and corner cuts all carry a side in their identity.

    Relabelling them would leave a card whose "front corners" were cut from its back — subtly
    wrong in a way nobody would notice until a buyer did.
    """
    assert ImageKind.PROCESSED_FRONT in swap_sides.DERIVED
    assert ImageKind.LISTING_FRONT in swap_sides.DERIVED
    for corner in (
        ImageKind.DETAIL_FRONT_TL,
        ImageKind.DETAIL_FRONT_TR,
        ImageKind.DETAIL_FRONT_BL,
        ImageKind.DETAIL_FRONT_BR,
    ):
        assert corner in swap_sides.DERIVED


def test_the_originals_are_kept():
    """Only their roles change — the photographs themselves are fine."""
    assert ImageKind.ORIGINAL_FRONT not in swap_sides.DERIVED
    assert ImageKind.ORIGINAL_BACK not in swap_sides.DERIVED


def test_both_files_are_read_before_either_is_written():
    """The two paths are about to hold each other's bytes; writing first destroys one."""
    source = inspect.getsource(swap_sides.swap)
    read_back = source.index("back_bytes = storage.get")
    first_write = source.index("storage.put(")
    assert read_back < first_write


def test_the_identity_is_cleared():
    """A card identified from the wrong side is identified wrongly."""
    source = inspect.getsource(swap_sides.swap)
    assert "item.card_id = None" in source
    assert "item.card_variant_id = None" in source


def test_detection_results_are_cleared_too():
    """Corners and confidence describe the old picture."""
    source = inspect.getsource(swap_sides.swap)
    for field in ("corners", "detection_confidence", "quality_verdict"):
        assert f"row.{field} = None" in source


def test_a_card_missing_a_side_is_refused():
    source = inspect.getsource(swap_sides.swap)
    assert "SwapError" in source


def test_reprocessing_is_queued_after_the_commit():
    """The worker reads these rows from the database; enqueueing earlier races the commit and
    can reprocess the bytes that were just replaced."""
    from app.routers import capture

    source = inspect.getsource(capture.swap_sides)
    assert source.index("await session.commit()") < source.index("_enqueue_processing")


def test_the_route_cannot_be_swallowed_by_the_side_parameter():
    """`POST /{sku}/{side}` matched "swap-sides" as a side name, silently. Depth is a sturdier
    guard than registration order."""
    from app.routers import capture

    paths = [r.path for r in capture.router.routes]
    assert any(p.endswith("/{sku}/sides/swap") for p in paths)
