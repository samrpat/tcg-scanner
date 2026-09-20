"""SKU formatting and path-traversal rejection."""

import pytest

from app.enums import ImageKind
from app.storage.paths import (
    UnsafePathError,
    format_sku,
    image_path,
    is_original,
    item_dir,
    parse_sku,
    validate_sku,
)


def test_sku_format_matches_the_spec():
    assert format_sku(1) == "CARD-000001"
    assert format_sku(2000) == "CARD-002000"
    assert format_sku(1234567) == "CARD-1234567"  # widens past six digits rather than truncating


def test_sku_round_trips():
    assert parse_sku(format_sku(421)) == 421


def test_sku_numbers_start_at_one():
    with pytest.raises(ValueError):
        format_sku(0)


def test_image_paths_match_the_spec_layout():
    assert image_path("CARD-000001", ImageKind.ORIGINAL_FRONT) == "CARD-000001/original-front.jpg"
    assert image_path("CARD-000001", ImageKind.ORIGINAL_BACK) == "CARD-000001/original-back.jpg"
    assert image_path("CARD-000001", ImageKind.PROCESSED_FRONT) == "CARD-000001/processed-front.jpg"
    assert image_path("CARD-000001", ImageKind.PROCESSED_BACK) == "CARD-000001/processed-back.jpg"


def test_item_dir_is_just_the_sku():
    assert item_dir("CARD-000042") == "CARD-000042"


@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd",
        "CARD-000001/../../../etc",
        "/CARD-000001",
        "CARD-abc",
        "CARD-",
        "",
        "CARD-000001 ; rm -rf /",
    ],
)
def test_traversal_and_malformed_skus_are_rejected(bad):
    with pytest.raises(UnsafePathError):
        validate_sku(bad)
    with pytest.raises(UnsafePathError):
        image_path(bad, ImageKind.ORIGINAL_FRONT)


def test_originals_are_identified_correctly():
    assert is_original(ImageKind.ORIGINAL_FRONT)
    assert is_original(ImageKind.ORIGINAL_BACK)
    assert not is_original(ImageKind.PROCESSED_FRONT)
