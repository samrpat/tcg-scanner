"""Signal fusion: the collector number corroborating, or contradicting, the picture."""

from dataclasses import dataclass

from app.imaging.ocr import CollectorNumber
from app.services.fusion import adjusted_weight, rescore


@dataclass
class FakeMatch:
    inliers: int


@dataclass
class FakeCard:
    tcgdex_id: str
    local_id: str | None = None


def reading(number, total, prefix=None, confidence=0.5):
    return CollectorNumber(number, total, "x", "bottom_right", confidence, prefix)


def test_set_total_breaks_a_tie_the_picture_cannot():
    """The real case: two Gyarados sharing artwork, 191 vs 181 inliers.

    Ancient Origins prints 98 cards, Generations 83. The capture reads 20/98.
    """
    scored = [
        (FakeMatch(191), FakeCard("g1-23", "23")),
        (FakeMatch(181), FakeCard("xy7-20", "20")),
    ]
    totals = {"g1-23": 83, "xy7-20": 98}

    reordered, fusion = rescore(scored, reading(20, 98), totals)

    assert reordered[0][1].tcgdex_id == "xy7-20"
    assert fusion.applied
    assert fusion.corroborated
    assert fusion.agreed is False


def test_agreement_requires_actual_corroboration():
    """A reading that supports nothing must not report agreement just because the order held.

    CARD-000014 read 35/113 while the top match was Zebstrika bw1-43. Reporting that as
    agreement is worse than reporting nothing.
    """
    scored = [(FakeMatch(13), FakeCard("bw1-43", "43"))]
    _, fusion = rescore(scored, reading(35, 113), {"bw1-43": 114})

    assert fusion.agreed is None
    assert not fusion.corroborated
    assert "corroborates no candidate" in fusion.note


def test_matching_number_and_total_both_count():
    card = FakeCard("bw11-35", "35")
    assert adjusted_weight(card, reading(35, 113), {"bw11-35": 113}) > 1.0
    # Zero-padded local ids are the same card.
    assert adjusted_weight(FakeCard("me04-005", "005"), reading(5, 86), {"me04-005": 86}) > 1.0


def test_a_wrong_total_penalises_rather_than_eliminates():
    card = FakeCard("g1-23", "23")
    weight = adjusted_weight(card, reading(20, 98), {"g1-23": 83})
    assert 0 < weight < 1.0


def test_no_reading_leaves_the_ranking_untouched():
    scored = [(FakeMatch(50), FakeCard("a-1")), (FakeMatch(10), FakeCard("b-2"))]
    reordered, fusion = rescore(scored, None, {})

    assert reordered == scored
    assert not fusion.applied


def test_a_promo_prefix_survives_into_the_key():
    assert reading(48, None, prefix="XY").key == "XY48"
    assert reading(35, 113).key == "35"
