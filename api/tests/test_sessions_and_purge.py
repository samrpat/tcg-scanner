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
    order to chance puts a corner crop on the search results page.

    The front leads, the back follows, the extra shots come next — on a holo that is the
    photograph that sells the card — and the corner close-ups come last.
    """
    from app.routers.sessions import download_photos, export_order

    labels = [label for _, label in export_order(corners=True, kind="listing")]
    assert labels == [
        "front",
        "back",
        "extra-1",
        "extra-2",
        "extra-3",
        "corner-top-left",
        "corner-top-right",
        "corner-bottom-left",
        "corner-bottom-right",
    ]

    # Numbered as they are written, not by a fixed slot. Most cards carry no extra shots, and
    # a fixed scheme leaves every one of them with a gap that reads as a missing photograph.
    source = inspect.getsource(download_photos)
    assert "position += 1" in source
    assert 'f"{item.sku}-{position}-{label}.jpg"' in source


def test_extra_shots_are_never_rectified():
    """What the operator framed by hand is the content. Running one through the detector would
    be undoing the only thing it is for — and asking it to find a card's edges in a photograph
    taken to show something other than the card's outline."""
    from app.services import extra_shots

    source = inspect.getsource(extra_shots.attach)
    assert "enqueue" not in source
    assert "process_image" not in source


def test_extra_shots_do_not_carry_the_phones_metadata():
    """These are the only images that reach a listing without passing through the pipeline, so
    they are the only ones that could upload a phone's EXIF — which on most phones includes
    where the photograph was taken."""
    from app.services import extra_shots

    source = inspect.getsource(extra_shots.normalise)
    # A new image is built from the pixels alone; nothing carries the original's metadata over.
    assert "exif_transpose" in source  # orientation baked in first...
    assert "exif=" not in source  # ...and then not written back out


def test_a_new_batch_follows_the_apps_mode():
    """Scanner mode is set once, not remembered per batch."""
    from app.routers.sessions import start_session

    assert "settings.scanner_mode" in inspect.getsource(start_session)


def test_the_download_follows_the_batchs_own_corner_setting():
    """Four extra images per card quadruples the upload; worth it on a card someone zooms
    into, dead weight on a bulk common.

    Unset must mean "whatever this batch was told to make", not "always include". The two
    disagreeing is how a batch with the switch off downloads six files per card anyway.
    """
    from app.enums import ImageKind
    from app.routers.sessions import download_photos, export_order

    parameter = inspect.signature(download_photos).parameters["corners"]
    assert parameter.default.default is None  # follows the batch
    assert "corners = target.corner_shots" in inspect.getsource(download_photos)

    with_corners = [kind for kind, _ in export_order(corners=True, kind="listing")]
    without = [kind for kind, _ in export_order(corners=False, kind="listing")]
    assert ImageKind.DETAIL_FRONT_TL in with_corners
    assert ImageKind.DETAIL_FRONT_TL not in without


def test_turning_corner_shots_off_never_deletes_anything():
    """A switch that quietly destroys work on its way past is one nobody can use with
    confidence. Deleting the files is a separate, explicit request."""
    from app.routers.sessions import delete_corner_shots, set_corner_shots

    source = inspect.getsource(set_corner_shots)
    # The build runs only on the way on...
    assert "if body.on:" in source
    # ...and nothing in the switch removes a stored image.
    assert "storage.delete" not in source
    assert "session.delete" not in source

    # The deliberate one does.
    assert "storage.delete" in inspect.getsource(delete_corner_shots)


def test_corners_are_cut_from_the_front_only():
    """Corners come from the front's listing render and the back carries none, so cutting on
    both sides re-encodes four images per card to reach an identical result."""
    from app.jobs.worker import _cut_corners_if_wanted

    source = inspect.getsource(_cut_corners_if_wanted)
    assert "ImageKind.ORIGINAL_FRONT" in source
    # And it must respect the batch, not cut unconditionally.
    assert "batch.corner_shots" in source


def test_the_whole_card_photographs_are_never_optional():
    """A listing with no picture of the card is not a listing."""
    from app.enums import ImageKind
    from app.routers.sessions import export_order

    # In every combination, not just the default one.
    for corners in (True, False):
        for kind in ("listing", "all"):
            kinds = [k for k, _ in export_order(corners=corners, kind=kind)]
            assert ImageKind.LISTING_FRONT in kinds
            assert ImageKind.LISTING_BACK in kinds


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


def test_cards_are_grouped_by_how_many_photographs_they_have():
    """A bulk uploader is handed a flat folder and told how many photographs each card has; it
    chunks the sorted list into groups of that size. One card with a seventh photograph shifts
    every card after it by one, and the result is a listing illustrated with someone else's
    card. So a batch carrying extra shots has to be uploaded as more than one.
    """
    from app.enums import ImageKind
    from app.routers.sessions import export_order, group_folder, photo_plan

    class FakeImage:
        def __init__(self, kind):
            self.kind = kind

    class FakeItem:
        def __init__(self, sku, kinds):
            self.sku = sku
            self.images = [FakeImage(k) for k in kinds]

    six = [
        ImageKind.LISTING_FRONT,
        ImageKind.LISTING_BACK,
        ImageKind.DETAIL_FRONT_TL,
        ImageKind.DETAIL_FRONT_TR,
        ImageKind.DETAIL_FRONT_BL,
        ImageKind.DETAIL_FRONT_BR,
    ]
    items = [
        FakeItem("CARD-000001", six),
        FakeItem("CARD-000002", [*six, ImageKind.EXTRA_1]),
        FakeItem("CARD-000003", six),
        FakeItem("CARD-000004", [*six, ImageKind.EXTRA_1, ImageKind.EXTRA_2]),
        FakeItem("CARD-000005", []),
    ]

    plan = photo_plan(items, export_order(corners=True, kind="listing"))
    assert [(g["photos"], g["cards"]) for g in plan["groups"]] == [(6, 2), (7, 1), (8, 1)]
    assert plan["groups"][0]["skus"] == ["CARD-000001", "CARD-000003"]

    # A card contributing nothing is reported, never silently dropped: finding out from a
    # listing with no picture is the expensive way.
    assert plan["without_photos"] == ["CARD-000005"]

    # The folder is named as the number to type into the uploader, because that is the only
    # thing anyone needs from it.
    assert group_folder(7) == "7-photos-per-card"


def test_turning_corners_off_changes_which_groups_come_out():
    """The count the uploader needs depends on the corner switch as much as on extra shots."""
    from app.enums import ImageKind
    from app.routers.sessions import export_order, photo_plan

    class FakeItem:
        def __init__(self, kinds):
            self.sku = "CARD-000001"
            self.images = [type("I", (), {"kind": k})() for k in kinds]

    item = FakeItem(
        [
            ImageKind.LISTING_FRONT,
            ImageKind.LISTING_BACK,
            ImageKind.DETAIL_FRONT_TL,
            ImageKind.EXTRA_1,
        ]
    )
    with_corners = photo_plan([item], export_order(corners=True, kind="listing"))
    without = photo_plan([item], export_order(corners=False, kind="listing"))
    assert with_corners["groups"][0]["photos"] == 4
    assert without["groups"][0]["photos"] == 3


# ── sections within a batch ────────────────────────────────────────────────────────────────


def test_deleting_a_section_keeps_its_cards():
    """A heading is a statement about grouping, never about cards. The same reasoning that
    makes deleting a batch keep its contents by default — losing an evening's scanning by
    tidying up a label would be unforgivable."""
    from app.routers.sessions import delete_section

    source = inspect.getsource(delete_section)
    assert "purge" not in source
    assert "cards_kept" in source
    # The foreign key does the work, and it must be SET NULL rather than CASCADE.
    from app.models import InventoryItem

    fk = next(iter(InventoryItem.__table__.c.section_id.foreign_keys))
    assert fk.ondelete == "SET NULL"


def test_new_cards_land_in_the_current_section():
    """The physical act is putting down one pile and picking up the next. A card should land
    in the section matching the pile in the operator's hand without them saying so per card."""
    from app.services.capture import capture

    source = inspect.getsource(capture)
    assert "current_section" in source
    assert "section_id=section.id if section else None" in source


def test_the_current_section_is_the_last_one_in_order():
    """"Last" rather than "most recently created", so reordering also changes where the next
    card lands — which is what somebody who just moved a section to the end expects."""
    from app.routers.sessions import current_section

    source = inspect.getsource(current_section)
    assert "BatchSection.position.desc()" in source


def test_a_section_name_cannot_invent_a_directory():
    """Section names are typed by hand and become folder names in the export. A name with a
    slash in it would otherwise add a level inside the zip that nobody asked for."""
    from app.routers.sessions import _safe_name

    assert "/" not in _safe_name("Reverse holo / NM")
    assert ".." not in _safe_name("../../etc/passwd")
    assert _safe_name("") == "section"
    assert _safe_name("   ") == "section"
    assert len(_safe_name("x" * 200)) <= 60
    # Ordinary names survive intact, because the point is a readable folder.
    assert _safe_name("Reverse holo - NM") == "Reverse holo - NM"


def test_the_two_download_splits_compose():
    """"Reverse holos separately" and "a fixed photo count per upload" are different questions
    about the same zip, so they must not be alternatives."""
    from app.routers.sessions import download_photos

    source = inspect.getsource(download_photos)
    assert "by_section" in source
    # Section is the outer folder; the count split nests inside it.
    assert 'path = f"{folders.get(item.section_id' in source


def test_where_scans_land_is_separate_from_how_sections_are_ordered():
    """"The last section" is right when you make one and wrong as soon as you want to go
    back. Finding three more reverse holos at the bottom of the box should not mean
    reordering the sections to say so — that expresses something about display order to
    change something that is not about display order at all."""
    from app.routers.sessions import current_section

    source = inspect.getsource(current_section)
    # The pointer wins...
    assert "batch.active_section_id" in source
    # ...and the old rule survives as the fallback, for a batch never pointed anywhere.
    assert "BatchSection.position.desc()" in source
    assert source.index("active_section_id") < source.index("position.desc()")


def test_a_pointer_at_a_deleted_section_becomes_no_section():
    """Not a dangling id. The column is on the batch and the section can be removed from
    under it."""
    from app.models import ScanSession

    fk = next(iter(ScanSession.__table__.c.active_section_id.foreign_keys))
    assert fk.ondelete == "SET NULL"


def test_creating_a_section_points_at_it():
    """You make a section because you are about to scan into it. Needing a second action to
    say the obvious thing is how a pile ends up in the previous section."""
    from app.routers.sessions import create_section

    assert "active_section_id = created.id" in inspect.getsource(create_section)


def test_the_scan_screen_learns_its_section_without_another_request():
    """It already polls `pending` every few seconds. A phone holding a camera open does not
    need a second poll to find out where its cards are going."""
    from app.routers.capture import pending

    source = inspect.getsource(pending)
    assert "active_section" in source
    assert "sections" in source
