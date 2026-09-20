"""The listing composer, checked against the operator's own sold listings.

The three titles asserted below are copied verbatim from listings that actually sold. They are
eBay's catalogue product names, which is what attaches a listing to the existing product page,
so reproducing them exactly is the whole point of the composer — a near miss creates a
detached duplicate listing that the price guide and product reviews do not reach.
"""

import pytest

from app.services.listing_draft import (
    build_specifics,
    build_title,
    extract_item_id,
    printed_number,
    sell_one_like_this,
)


@pytest.mark.parametrize(
    ("name", "local_id", "total", "set_name", "variant", "series", "series_name", "expected"),
    [
        # https://www.ebay.com/itm/398334128413
        ("Gengar", "70", 214, "Unbroken Bonds", "Normal", "sm", "Sun & Moon",
         "Gengar 70/214 Sm-Unbroken Bonds Regular"),
        # https://www.ebay.com/itm/398334123701
        ("Snorlax", "SWSH119", 307, "SWSH Black Star Promos", "Holo", "swsh", "Sword & Shield",
         "Snorlax SWSH119 SWSH: Sword & Shield Promo Cards Holo"),
        # https://www.ebay.com/itm/398333961123
        ("Unown G", "57", 106, "Great Encounters", "Normal", "dp", "Diamond & Pearl",
         "Unown [G] 57/106 Great Encounters Regular"),
    ],
)
def test_title_matches_sold_listing(
    name, local_id, total, set_name, variant, series, series_name, expected
):
    got = build_title(
        name, local_id, total, set_name, variant,
        series_id=series, series_name=series_name,
    )
    assert got == expected


def test_title_omits_condition_and_rarity():
    """Both are item specifics with their own search facets; repeating them costs characters."""
    title = build_title(
        "Gengar", "70", 214, "Unbroken Bonds", "Normal",
        condition="LP", rarity="Rare", series_id="sm",
    )
    assert "Rare" not in title
    assert "Lightly" not in title and "LP" not in title


def test_title_fits_ebays_limit():
    title = build_title(
        "Charizard VMAX Rainbow Secret Rare", "074", 73,
        "Champions Path Ultra Premium Collection", "Reverse Holo", series_id="swsh",
    )
    assert len(title) <= 80
    # The identity survives; the finish is what gets dropped.
    assert title.startswith("Charizard VMAX Rainbow Secret Rare 074/073")


def test_promo_number_has_no_set_total():
    """A promo's number is printed bare — "SWSH119", never "SWSH119/307"."""
    assert printed_number("SWSH119", 307) == "SWSH119"


def test_number_pads_to_the_collector_numbers_own_width():
    assert printed_number("70", 214) == "70/214"
    assert printed_number("29", 39) == "29/39"      # not 29/039
    assert printed_number("021", 86) == "021/086"


def test_sell_similar_not_relist():
    """`RelistItem` resurrects the sold listing; a different physical card needs a new one."""
    url = sell_one_like_this("398334128413")
    assert url == "https://www.ebay.com/sl/list?itemId=398334128413&mode=SellSimilarItem"


def test_specifics_match_the_sold_listing():
    got = build_specifics(
        "Gengar", "70", 214, "Unbroken Bonds", "Normal",
        rarity="Rare", hp=130, stage="Stage2", illustrator="so-taro",
        category="Pokemon", series_id="sm", series_name="Sun & Moon",
        condition_label="Lightly played (Excellent)",
    )
    assert got["Set"] == "Sm-Unbroken Bonds"
    assert got["Card Number"] == "70/214"
    assert got["Finish"] == "Regular"
    assert got["Stage"] == "Stage 2"            # TCGdex writes "Stage2"
    assert got["Card Type"] == "Pokémon"        # TCGdex drops the accent
    assert got["Game"] == "Pokémon TCG"


def test_specifics_omit_what_is_unknown():
    """A wrong aspect is worse than a missing one."""
    got = build_specifics("Gengar", "70", 214, "Unbroken Bonds", "Normal")
    assert "HP" not in got
    assert "Illustrator" not in got
    assert "Rarity" not in got


def test_promo_set_name_is_derived_not_guessed():
    """Only the Sword & Shield mapping is evidenced; the rest follow from the series name.

    `is_promo_set` is what the UI uses to ask for a second look at these, precisely because
    they are inferred.
    """
    from app.services.listing_draft import ebay_set_name, is_promo_set

    assert ebay_set_name("SWSH Black Star Promos", "swsh", "Sword & Shield") == (
        "SWSH: Sword & Shield Promo Cards"
    )
    assert ebay_set_name("SVP Black Star Promos", "sv", "Scarlet & Violet") == (
        "SVP: Scarlet & Violet Promo Cards"
    )
    # The code and the series are the same word, so the colon form would stutter.
    assert ebay_set_name("XY Black Star Promos", "xy", "XY") == "XY Promo Cards"
    assert is_promo_set("SWSH Black Star Promos")
    assert not is_promo_set("Unbroken Bonds")


def test_non_promo_sets_keep_their_name():
    from app.services.listing_draft import ebay_set_name

    assert ebay_set_name("Great Encounters", "dp", "Diamond & Pearl") == "Great Encounters"
    assert ebay_set_name("Unbroken Bonds", "sm", "Sun & Moon") == "Sm-Unbroken Bonds"


def test_item_id_from_a_pasted_url():
    assert extract_item_id("https://www.ebay.com/itm/398334128413?hash=x") == "398334128413"
    assert extract_item_id("398334128413") == "398334128413"
    assert extract_item_id("nothing here") is None
