"""Reading eBay sold listings.

Every test here is about not pricing a card off the wrong comp. The two ways that happens are
using a different product — a graded slab, a hundred-card lot — and using a different condition.
Both produce a number that looks plausible and is wrong by a multiple, which is worse than
having no number at all.
"""

import pytest

from app.services.ebay_sold import condition_of, is_comparable, parse_sales


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("Ungraded - Near mint or better: Not in original packaging", "NM"),
        ("Ungraded - Excellent: Not in original packaging", "LP"),
        ("Ungraded - Very good: Not in original packaging", "MP"),
        ("Ungraded - Good", "HP"),
        ("Ungraded - Poor", "DMG"),
    ],
)
def test_ebays_own_condition_labels_are_read(field, expected):
    """These are the labels on the operator's own sold listings."""
    assert condition_of("Some Card 12/34", field) == expected


def test_the_stated_condition_beats_the_title():
    """A seller who set the field chose from a list; a title is free text."""
    assert condition_of("Gengar NM MINT!!", "Ungraded - Very good") == "MP"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Gengar 70/214 Near Mint", "NM"),
        ("Gengar 70/214 NM", "NM"),
        ("Gengar 70/214 lightly played", "LP"),
        ("Gengar 70/214 LP", "LP"),
        ("Gengar 70/214 Moderately Played", "MP"),
        ("Gengar 70/214 heavily played", "HP"),
        ("Gengar 70/214 damaged", "DMG"),
    ],
)
def test_grades_written_into_titles_are_read(title, expected):
    assert condition_of(title, None) == expected


def test_near_mint_wins_over_mint():
    """Longest match first, or every "near mint" listing reads as "mint"."""
    assert condition_of("Charizard near mint condition", None) == "NM"


def test_an_unstated_condition_is_unknown_not_assumed():
    """Defaulting to Near Mint here would silently price every vague listing as the best case."""
    assert condition_of("Gengar 70/214 Unbroken Bonds Pokemon", None) is None


@pytest.mark.parametrize(
    "title",
    [
        "PSA 10 Charizard Base Set",
        "BGS 9.5 Gengar",
        "CGC 8 Pikachu",
        "Charizard GRADED gem mt",
    ],
)
def test_graded_slabs_never_price_a_raw_card(title):
    """A slab sells for a multiple of the raw card; one in the median wrecks it."""
    assert is_comparable(title) is False


@pytest.mark.parametrize(
    "title",
    [
        "Pokemon lot of 100 cards",
        "Charizard bundle",
        "Gengar playset x4",
        "Sealed booster box",
        "Pick your card - choose any",
        "Bulk Pokemon collection binder",
    ],
)
def test_multi_card_listings_never_price_one_card(title):
    assert is_comparable(title) is False


def test_an_ordinary_single_is_comparable():
    assert is_comparable("Gengar 70/214 Sm-Unbroken Bonds Regular") is True


def test_parse_keeps_the_usable_and_drops_the_rest():
    rows = [
        {"title": "Gengar 70/214 NM", "price": 18.0, "condition": "Ungraded - Excellent",
         "url": "https://www.ebay.com/itm/398334128413"},
        {"title": "PSA 10 Gengar 70/214", "price": 400.0},      # graded
        {"title": "Pokemon lot of 50", "price": 12.0},           # not a single
        {"title": "Gengar 70/214", "price": 0},                  # no price
        {"title": "", "price": 5.0},                             # no title
        {"title": "Gengar 70/214 MP", "price": "not a number"},  # unparseable
    ]
    sales = parse_sales(rows, "Gengar 70/214")
    assert len(sales) == 1
    only = sales[0]
    assert only["price"] == 18.0
    # The stated field wins, so this is LP rather than the NM in the title.
    assert only["condition"] == "LP"
    assert only["item_id"] == "398334128413"
    assert only["query"] == "Gengar 70/214"


def test_the_query_identifies_one_printing():
    """Loose queries price the wrong card; the number is quoted so it matches as a unit."""
    from types import SimpleNamespace

    from app.services.ebay_sold_job import query_for

    card = SimpleNamespace(
        name="Gengar",
        local_id="70",
        card_set=SimpleNamespace(
            name="Unbroken Bonds",
            card_count_official=214,
            series_id="sm",
            series_name="Sun & Moon",
        ),
    )
    assert query_for(card, "Normal") == 'Gengar "70/214" Sm-Unbroken Bonds'
    # A reverse holo is a different printing at a different price.
    assert "reverse holo" in query_for(card, "Reverse Holo")
