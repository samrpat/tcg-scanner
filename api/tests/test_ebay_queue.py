"""The eBay listing queue's decision logic.

Following this suite's convention, only the parts that need no database are exercised here; the
endpoints themselves are covered by `make smoke` against the running stack.

What is worth testing without a database is the part that decides whether a card may be listed.
A queue that wrongly calls a card ready produces a bad listing; one that wrongly calls it blocked
hides a card in a pile of two thousand, and nobody finds it again.
"""

from types import SimpleNamespace

import pytest

from app.enums import Condition
from app.routers.ebay import _blockers
from app.services.listing_draft import ebay_set_name, is_promo_set


def item(**over):
    """A listable card, minus whatever the test takes away."""
    base = dict(
        card=SimpleNamespace(name="Gengar"),
        card_variant_id="v1",
        approved_at="2026-09-01",
        condition=Condition.NM,
        lot_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_a_complete_card_has_nothing_blocking():
    assert _blockers(item(), price=1.50) == []


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ({"card": None}, "not identified yet"),
        ({"card_variant_id": None}, "pick which version it is"),
        ({"approved_at": None}, "not approved yet"),
        ({"condition": None}, "set the condition"),
    ],
)
def test_each_missing_piece_is_named(missing, expected):
    assert expected in _blockers(item(**missing), price=1.50)


def test_a_card_with_no_price_is_not_ready():
    assert "no price yet" in _blockers(item(), price=None)


def test_a_card_in_a_lot_is_not_listed_alone():
    """It is being sold as part of the lot; listing it singly would sell it twice."""
    assert any("lot" in b for b in _blockers(item(lot_id="l1"), price=1.50))


def test_every_problem_is_reported_at_once():
    """Fixing one thing and coming back to find another is what makes a queue feel endless."""
    reasons = _blockers(
        item(card=None, card_variant_id=None, approved_at=None, condition=None),
        price=None,
    )
    assert len(reasons) == 5


def test_promo_sets_are_flagged_for_a_second_look():
    """Only the Sword & Shield naming is evidenced by a real listing; the rest is inferred."""
    assert is_promo_set("SVP Black Star Promos")
    assert not is_promo_set("Unbroken Bonds")
    assert ebay_set_name("SWSH Black Star Promos", "swsh", "Sword & Shield") == (
        "SWSH: Sword & Shield Promo Cards"
    )
