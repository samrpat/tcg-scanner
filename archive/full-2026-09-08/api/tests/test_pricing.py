"""Price extraction from TCGdex payloads."""

import pytest

from app.enums import PriceType
from app.services.pricing import (
    SOURCE_CARDMARKET,
    SOURCE_MANUAL,
    SOURCE_TCGPLAYER,
    ManualPricingProvider,
    TCGdexPricingProvider,
)
from app.services.tcgdex import normalize_variants


@pytest.fixture
def pricing_block(base_charizard) -> dict:
    priced = [v for v in normalize_variants(base_charizard) if v["pricing"]]
    return priced[0]["pricing"]


async def test_both_marketplaces_are_captured(pricing_block):
    # base1-4 Charizard is a holo, so its TCGplayer block carries only "holofoil".
    observations = await TCGdexPricingProvider().observe(pricing_block, "holo")
    sources = {o.source_code for o in observations}
    assert SOURCE_TCGPLAYER in sources
    assert SOURCE_CARDMARKET in sources


async def test_currencies_are_kept_distinct(pricing_block):
    observations = await TCGdexPricingProvider().observe(pricing_block, "holo")
    usd = {o.currency for o in observations if o.source_code == SOURCE_TCGPLAYER}
    eur = {o.currency for o in observations if o.source_code == SOURCE_CARDMARKET}
    assert usd == {"USD"}
    assert eur == {"EUR"}


async def test_timestamps_are_recorded(pricing_block):
    observations = await TCGdexPricingProvider().observe(pricing_block, "holo")
    assert any(o.source_updated_at is not None for o in observations)


async def test_market_price_is_extracted(pricing_block):
    observations = await TCGdexPricingProvider().observe(pricing_block)
    markets = [o for o in observations if o.price_type is PriceType.MARKET]
    assert markets
    assert all(o.amount > 0 for o in markets)


async def test_missing_and_zero_prices_become_absent_rows_not_zeroes():
    """A null price is an absent observation, never a 0.00 that would poison averages."""
    empty = await TCGdexPricingProvider().observe(
        {"cardmarket": {"unit": "EUR", "avg": None, "low": 0, "trend": 5.0}}
    )
    assert [o.price_type for o in empty] == [PriceType.TREND]


async def test_no_pricing_block_yields_nothing():
    assert await TCGdexPricingProvider().observe({}) == []


async def test_manual_provider_round_trips():
    observations = await ManualPricingProvider().observe({"amount": 12.5, "currency": "USD"})
    assert len(observations) == 1
    assert observations[0].source_code == SOURCE_MANUAL
    assert observations[0].amount == 12.5


async def test_manual_provider_ignores_an_empty_entry():
    assert await ManualPricingProvider().observe({}) == []


# --- printing separation ---------------------------------------------------------------
#
# TCGdex hands the *same* pricing block to every variant of a card. It describes all printings,
# and reading it without regard to which one is being priced writes a normal card's price against
# a reverse holo. Measured on me04-037: $0.08 normal against $0.24 reverse-holofoil.

TWO_PRINTINGS = {
    "tcgplayer": {
        "unit": "USD",
        "updated": "2026-09-01T09:34:16.999Z",
        "normal": {"marketPrice": 0.08, "lowPrice": 0.01},
        "reverse-holofoil": {"marketPrice": 0.24, "lowPrice": 0.01},
    },
    "cardmarket": {
        "unit": "EUR",
        "updated": "2026-09-01T09:33:39.806Z",
        "avg": 0.03,
        "avg-holo": 0.09,
    },
}


def _market(observations, source):
    return [
        o.amount
        for o in observations
        if o.source_code == source and o.price_type is PriceType.MARKET
    ]


async def test_each_printing_gets_only_its_own_tcgplayer_price():
    provider = TCGdexPricingProvider()
    normal = await provider.observe(TWO_PRINTINGS, "normal")
    reverse = await provider.observe(TWO_PRINTINGS, "reverse")

    assert _market(normal, SOURCE_TCGPLAYER) == [0.08]
    assert _market(reverse, SOURCE_TCGPLAYER) == [0.24]


async def test_cardmarket_holo_suffix_is_used_for_foil_printings():
    """Cardmarket keeps one block and suffixes the foil figures with "-holo"."""
    provider = TCGdexPricingProvider()
    normal = await provider.observe(TWO_PRINTINGS, "normal")
    reverse = await provider.observe(TWO_PRINTINGS, "reverse")

    assert _market(normal, SOURCE_CARDMARKET) == [0.03]
    assert _market(reverse, SOURCE_CARDMARKET) == [0.09]


async def test_foil_falls_back_when_a_card_has_only_one_cardmarket_figure():
    """Cards printed in a single finish carry no "-holo" keys; the plain figure still applies."""
    single = {"cardmarket": {"unit": "EUR", "avg": 1.5}}
    observations = await TCGdexPricingProvider().observe(single, "reverse")
    assert _market(observations, SOURCE_CARDMARKET) == [1.5]


# --- listing economics ------------------------------------------------------------------
#
# Shipping is the term that decides everything at the cheap end. A card that "sells for $0.24"
# costs $1.30 to post, so the interesting question is not what multiplier LP deserves but
# whether the card belongs in a listing or a bulk lot at all.

from app.config import settings  # noqa: E402
from app.services.pricing import _break_even, _net_proceeds  # noqa: E402


def test_buyer_paid_shipping_still_costs_a_fee_on_the_postage():
    """The postage is reimbursed, but eBay charges its percentage on it all the same."""
    assert settings.ebay_buyer_pays_shipping
    # A 25c card cannot cover the fixed fee plus the fee levied on $1.30 of postage.
    assert _net_proceeds(0.25) < 0


def test_break_even_is_where_proceeds_reach_zero():
    assert _net_proceeds(_break_even()) == pytest.approx(0, abs=0.01)


def test_buyer_paid_shipping_has_a_far_lower_break_even_than_free_shipping():
    """Who pays postage is the difference between most of a bulk box being sellable or not."""
    buyer = _break_even()
    settings.ebay_buyer_pays_shipping = False
    try:
        free = _break_even()
    finally:
        settings.ebay_buyer_pays_shipping = True
    assert buyer < free
    assert buyer < 1.0 < free


def test_a_real_single_clears_comfortably():
    assert _net_proceeds(30.00) > 20


def test_condition_multipliers_descend_and_never_exceed_near_mint():
    order = ["NM", "LP", "MP", "HP", "DMG"]
    values = [settings.price_condition_multipliers[c] for c in order]
    assert values == sorted(values, reverse=True)
    assert values[0] == 1.0


# --- eBay is not TCGplayer ---------------------------------------------------------------
#
# The two markets diverge hardest at the cheap end. TCGplayer quotes $0.15 for a common because
# its sellers move them in hundreds; nobody lists one card on eBay for fifteen cents, so the same
# card changes hands near a dollar. Judging "worth listing?" against the TCGplayer figure
# rejected cards that demonstrably sell — observed on Frogadier 021/086, quoted at $0.15 with
# completed eBay listings at $0.99, $1.29, $1.59, $1.99 and $2.70.


def test_a_cheap_feed_price_is_raised_to_ebays_floor():
    assert settings.ebay_floor_price > 0.5
    # A card the feed values at 15c still clears its costs at eBay's floor.
    assert _net_proceeds(settings.ebay_floor_price) > 0


def test_the_floor_does_not_drag_a_real_price_down():
    """The floor is a floor, not a target — a card worth more keeps its own price."""
    assert max(5.00, settings.ebay_floor_price) == 5.00


def test_a_floored_card_is_not_written_off_as_bulk():
    """The regression this guards: a 15c feed price used to read 'bulk' and be skipped."""
    assert _net_proceeds(settings.ebay_floor_price) > 0
    assert _net_proceeds(0.15) < 0  # what the feed alone would have implied


# --- lot economics ------------------------------------------------------------------------
#
# The fixed costs of an eBay order — the per-order fee and the postage — are paid once per
# ORDER, not per card. That is the whole reason a lot is worth more than its cards sold singly,
# and it is what makes an otherwise unsellable bulk box worth listing.

from app.routers.inventory import lot_economics  # noqa: E402


def test_a_lot_beats_selling_the_same_cards_separately():
    cards = [0.99] * 12
    e = lot_economics(cards)
    assert e["net"] > e["net_if_sold_separately"]
    assert e["advantage"] > 0


def test_the_advantage_grows_with_the_number_of_cards():
    """Each extra card in a lot avoids one more fixed fee."""
    small = lot_economics([0.99] * 3)["advantage"]
    large = lot_economics([0.99] * 20)["advantage"]
    assert large > small


def test_a_single_card_lot_has_no_advantage():
    e = lot_economics([5.00])
    assert e["advantage"] == 0


def test_an_empty_lot_is_harmless():
    e = lot_economics([])
    assert e["cards"] == 0
    assert e["suggested_price"] == 0
