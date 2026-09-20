"""Marketplace condition translation."""

import pytest

from app.conditioning.translate import (
    DEFAULT_TRANSLATIONS,
    EBAY_CARD_CONDITION_DESCRIPTOR_ID,
    EBAY_CONDITION_ID_UNGRADED,
    seed_rows,
    translate,
)
from app.enums import Condition, Marketplace


def test_every_condition_maps_on_every_marketplace():
    for marketplace in Marketplace:
        for condition in Condition:
            assert translate(condition, marketplace).external_code


@pytest.mark.parametrize(
    ("condition", "code"),
    [
        (Condition.NM, "400010"),
        (Condition.LP, "400011"),
        (Condition.MP, "400012"),
        (Condition.HP, "400013"),
        (Condition.DMG, "400013"),
    ],
)
def test_ebay_uses_the_four_ungraded_descriptor_values(condition, code):
    assert translate(condition, Marketplace.EBAY).external_code == code


def test_ebay_collapses_hp_and_dmg_onto_poor():
    """eBay offers four buckets against our five, so this collapse is expected, not a bug."""
    hp = translate(Condition.HP, Marketplace.EBAY)
    dmg = translate(Condition.DMG, Marketplace.EBAY)
    assert hp.external_code == dmg.external_code
    assert dmg.requires_photo and not hp.requires_photo


def test_ebay_descriptor_constants():
    assert EBAY_CONDITION_ID_UNGRADED == "4000"
    assert EBAY_CARD_CONDITION_DESCRIPTOR_ID == "40001"


def test_tcgplayer_uses_our_codes_directly():
    for condition in Condition:
        assert translate(condition, Marketplace.TCGPLAYER).external_code == condition.value


def test_collectr_uses_full_words_not_codes():
    """Verified against a real Collectr CSV export: the Card Condition column reads
    "Near Mint", not "NM". Emitting the code would fail their import."""
    assert translate(Condition.NM, Marketplace.COLLECTR).external_code == "Near Mint"
    assert translate(Condition.MP, Marketplace.COLLECTR).external_code == "Moderately Played"
    assert translate(Condition.DMG, Marketplace.COLLECTR).external_code == "Damaged"


def test_cardmarket_scale_is_shifted_not_identical():
    """Cardmarket's seven tiers are stricter, so LP maps to EX rather than LP."""
    assert translate(Condition.LP, Marketplace.CARDMARKET).external_code == "EX"
    assert translate(Condition.MP, Marketplace.CARDMARKET).external_code == "GD"


def test_seed_rows_cover_the_full_matrix():
    rows = seed_rows()
    assert len(rows) == len(Marketplace) * len(Condition)
    assert len(DEFAULT_TRANSLATIONS) == len(Marketplace)
