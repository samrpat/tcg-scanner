"""Scanning sessions and deleting cards.

The delete path is the one that matters. It removes photographs from disk, which cannot be
rolled back, so the ordering and the defaults are the whole design: rows first inside the
transaction, files only after it commits, and never files for a delete that failed.
"""

import inspect

from app.services import purge


def test_rows_are_deleted_in_bulk_not_one_by_one():
    """`session.delete(obj)` makes SQLAlchemy null the children's foreign keys first, which
    fails outright against `condition_assessments.inventory_item_id NOT NULL`. The database
    already declares ON DELETE CASCADE; a bulk statement lets it do that."""
    source = inspect.getsource(purge.purge_rows)
    assert "delete(InventoryItem)" in source
    assert "session.delete(" not in source


def test_purging_rows_never_touches_the_filesystem():
    """Deleting a directory cannot be rolled back, so it must not happen inside a transaction
    that might fail — which is exactly what left cards in the database with no photographs."""
    source = inspect.getsource(purge.purge_rows)
    assert "storage" not in source
    assert "delete_prefix" not in source


def test_images_are_deleted_by_a_separate_call():
    assert "delete_prefix" in inspect.getsource(purge.purge_images)


def test_deleting_a_session_keeps_its_cards_by_default():
    """Removing a grouping is not removing the cards, and the default must not conflate them."""
    from app.routers.sessions import delete_session

    default = inspect.signature(delete_session).parameters["cards"].default
    assert getattr(default, "default", default) == "keep"


def test_a_card_is_never_scanned_into_no_session():
    """Someone who never opens the sessions screen still gets a batch."""
    from app.services import capture

    assert "ensure_open_session" in inspect.getsource(capture)


def test_storage_can_remove_a_whole_item_directory():
    """SKUs are reallocated from max(sku)+1, so a stale directory becomes the next card's."""
    from app.storage.local import LocalStorage

    assert hasattr(LocalStorage, "delete_prefix")


def test_deleting_a_prefix_goes_through_the_traversal_guard():
    """A prefix is as user-influenced as a path, and this one removes a directory tree."""
    from app.storage.local import LocalStorage

    assert "_resolve(prefix)" in inspect.getsource(LocalStorage.delete_prefix)


def test_archiving_keeps_everything_and_only_stops_new_scans():
    """"Done with that pile" means new cards go elsewhere — not that the pile is destroyed."""
    from app.routers.sessions import archive_session

    source = inspect.getsource(archive_session)
    assert "closed_at" in source
    # No delete of any kind on this path.
    assert "purge" not in source
    assert "session.delete" not in source


def test_archiving_opens_the_next_batch_in_the_same_breath():
    """Otherwise the next card scanned lands nowhere and the operator has to notice."""
    from app.routers.sessions import archive_session

    assert "ScanSession(" in inspect.getsource(archive_session)


def test_the_photo_zip_is_flat_by_default():
    """One folder of files is what another uploader wants to be pointed at, and the SKU is
    already in every filename."""
    from app.routers.sessions import download_photos

    assert inspect.signature(download_photos).parameters["layout"].default.default == "flat"


def test_photo_filenames_carry_their_order():
    """Uploaders sort by filename and the first becomes the gallery image, so leaving the
    order to chance puts a corner crop on the search results page."""
    source = inspect.getsource(download_photos_source := __import__(
        "app.routers.sessions", fromlist=["download_photos"]
    ).download_photos)
    for label in ("1-front", "2-back", "3-corner-top-left", "6-corner-bottom-right"):
        assert label in source
    assert download_photos_source is not None


def test_a_new_batch_follows_the_apps_mode():
    """Scanner mode is set once, not remembered per batch."""
    from app.routers.sessions import start_session

    assert "settings.scanner_mode" in inspect.getsource(start_session)


def test_corner_shots_can_be_left_out_of_the_export():
    """Four extra images per card quadruples the upload; worth it on a card someone zooms
    into, dead weight on a bulk common."""
    from app.routers.sessions import download_photos

    parameter = inspect.signature(download_photos).parameters["corners"]
    assert parameter.default.default is True  # on unless asked otherwise

    source = inspect.getsource(download_photos)
    # The corner kinds must be inside the conditional, not appended unconditionally.
    assert "if corners:" in source
    before, after = source.split("if corners:", 1)
    assert "DETAIL_FRONT_TL" not in before
    assert "DETAIL_FRONT_TL" in after


def test_the_whole_card_photographs_are_never_optional():
    """A listing with no picture of the card is not a listing."""
    from app.routers.sessions import download_photos

    before = inspect.getsource(download_photos).split("if corners:", 1)[0]
    assert "LISTING_FRONT" in before
    assert "LISTING_BACK" in before


def test_removing_corner_shots_touches_only_corner_shots():
    """They are derived and cheap to remake; the originals and renders must not follow them."""
    from app.routers.ebay import remove_corner_details

    source = inspect.getsource(remove_corner_details)
    assert "corner_details.QUADRANTS" in source
    for protected in ("ORIGINAL_FRONT", "LISTING_FRONT", "PROCESSED_FRONT"):
        assert protected not in source


def test_inventory_can_be_narrowed_to_one_batch():
    """The batch screen showed the whole collection regardless of batch, so archiving cleared
    nothing — which is the entire point of archiving."""
    from app.routers.inventory import list_inventory

    assert "session_id" in inspect.signature(list_inventory).parameters
    source = inspect.getsource(list_inventory)
    assert "item.session_id != session_id" in source


def test_a_card_reports_which_batch_it_is_in():
    """Without it the UI cannot tell an archived batch's cards from the current ones."""
    assert '"session_id"' in inspect.getsource(
        __import__("app.routers.inventory", fromlist=["list_inventory"]).list_inventory
    )
