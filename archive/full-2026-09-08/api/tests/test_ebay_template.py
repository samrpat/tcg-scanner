"""Matching this system's values onto eBay's own template columns.

eBay hands the seller a template, and its columns are the ones it will accept. They differ by
template type and category, and they are not what the File Exchange documentation implies. The
headers asserted here are copied from templates the operator actually downloaded.
"""

import pytest

from app.services.ebay_template import field_for, normalise, parse_template, row_for, unmatched

# From eBay-draft-listing-template_US.
DRAFT_HEADERS = [
    "Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)",
    "Custom label (SKU)", "Category ID", "Title", "UPC", "Price",
    "Quantity", "Item photo URL", "Condition ID", "Description", "Format",
]

# From ebay_trading_card_ccg.csv — the My Collection template.
COLLECTION_HEADERS = [
    "Title (required field)", "Game (required field)",
    "Purchase Price (Price in USD, numbers only)", "Purchase Date MM-DD-YYYY",
    "Card Name", "Set", "Rarity", "Finish", "Card Number", "Language",
    "Graded (Y/N)", "Card Condition", "Professional Grader", "Grade",
    "Certification Number", "Features", "Year Manufactured",
]


@pytest.mark.parametrize(
    ("header", "field"),
    [
        ("Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)", "action"),
        ("Custom label (SKU)", "sku"),
        ("Category ID", "category"),
        ("Item photo URL", "pics"),
        ("Condition ID", "condition_id"),
        ("Price", "price"),
        ("*Title", "title"),          # eBay marks required columns with a star
        ("PicURL", "pics"),            # the File Exchange spelling of the same column
    ],
)
def test_headers_are_matched_by_meaning_not_spelling(header, field):
    """A listing that loses its photographs to a capitalisation difference is the failure
    this module exists to prevent."""
    assert field_for(header) == field


def test_the_version_string_does_not_hide_the_action_column():
    assert normalise("Action(SiteID=US|Country=US|Version=1193)") == "action"


def test_prefixed_and_bare_item_specifics_both_resolve():
    """The listing templates write `C:Rarity`; the collection template writes `Rarity`."""
    assert field_for("C:Rarity") == "aspect:Rarity"
    assert field_for("Rarity") == "aspect:Rarity"
    assert field_for("Card Number") == "aspect:Card Number"


def test_the_draft_template_is_fully_understood():
    unknown = [h for h in DRAFT_HEADERS if not field_for(h)]
    assert unknown == []


def test_the_collection_template_leaves_only_what_we_must_not_invent():
    """Purchase price and date are the operator's cost basis; grading fields describe a slab.

    Filling any of them would be fabricating a record, so they stay empty by design.
    """
    unknown = [h for h in COLLECTION_HEADERS if not field_for(h)]
    assert set(unknown) == {
        "Purchase Price (Price in USD, numbers only)",
        "Purchase Date MM-DD-YYYY",
        "Professional Grader",
        "Grade",
        "Certification Number",
    }


def test_a_row_covers_every_column_even_the_empty_ones():
    """eBay reads by position as well as by name, so a short row is a misaligned row."""
    row = row_for(DRAFT_HEADERS, {"title": "Gengar", "price": "1.41"}, {})
    assert list(row) == DRAFT_HEADERS
    assert row["Title"] == "Gengar"
    assert row["UPC"] == ""


def test_what_the_template_cannot_carry_is_reported():
    """A dropped Rarity column is a listing that will not appear under a rarity filter."""
    missing = unmatched(DRAFT_HEADERS, {}, {"Rarity": "Ultra Rare", "Set": "Chaos Rising"})
    assert "C:Rarity" in missing and "C:Set" in missing


def test_the_header_row_is_found_beneath_ebays_preamble():
    text = (
        "#INFO,Version=0.0.2,Template= eBay-draft-listings-template_US\n"
        "#INFO Action and Category ID are required fields.\n"
        + ",".join(DRAFT_HEADERS)
        + "\n"
    )
    parsed = parse_template(text)
    assert parsed["headers"] == DRAFT_HEADERS
    assert parsed["preamble"].startswith("#INFO")


def test_a_file_that_is_not_a_template_is_refused():
    with pytest.raises(ValueError, match="header row"):
        parse_template("just,some,random,columns\n1,2,3,4\n")
