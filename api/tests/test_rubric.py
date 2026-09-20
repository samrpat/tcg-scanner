"""The rubric itself: ceilings, thresholds and the not-allowed lists."""

import pytest

from app.conditioning.rubric import (
    CARD_AREA_MM2,
    CONDITION_CEILINGS,
    RUBRIC,
    SEVERITY_POINTS,
    best_condition_for,
    condition_for_points,
    seed_rows,
    severity_for_measurement,
    worse_of,
)
from app.enums import Condition, Severity


def test_severity_point_values():
    assert SEVERITY_POINTS == {
        Severity.SLIGHT: 1,
        Severity.MINOR: 2,
        Severity.MODERATE: 4,
        Severity.MAJOR: 8,
    }


def test_condition_ceilings_match_the_standard():
    assert CONDITION_CEILINGS[Condition.NM] == 3
    assert CONDITION_CEILINGS[Condition.LP] == 6
    assert CONDITION_CEILINGS[Condition.MP] == 12
    assert CONDITION_CEILINGS[Condition.HP] == 24


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        (0, Condition.NM),
        (3, Condition.NM),
        (4, Condition.LP),   # boundary: one point past NM
        (6, Condition.LP),
        (7, Condition.MP),
        (12, Condition.MP),
        (13, Condition.HP),
        (24, Condition.HP),
        (25, Condition.DMG), # boundary: past every ceiling
        (99, Condition.DMG),
    ],
)
def test_points_map_to_conditions_at_every_boundary(points, expected):
    assert condition_for_points(points) is expected


def test_card_area_is_the_standard_value():
    assert CARD_AREA_MM2 == 5544.0


@pytest.mark.parametrize(
    ("imperfection", "expected"),
    [
        ("surface_wear", Condition.LP),  # not allowed at NM
        ("grime", Condition.LP),         # not allowed at NM
        ("bend", Condition.LP),          # not allowed at NM
        ("fault", Condition.MP),         # not allowed at NM or LP
        ("damage", Condition.DMG),       # never allowed above DMG
    ],
)
def test_not_allowed_lists_impose_a_floor(imperfection, expected):
    """A single instance of these can never grade better than `expected`, however slight."""
    assert best_condition_for(imperfection, Severity.SLIGHT) is expected


def test_edgewear_is_tolerated_at_near_mint():
    assert best_condition_for("edgewear", Severity.SLIGHT) is Condition.NM


def test_worse_of_picks_the_lower_condition():
    assert worse_of(Condition.NM, Condition.MP) is Condition.MP
    assert worse_of(Condition.DMG, Condition.NM) is Condition.DMG
    assert worse_of(Condition.LP, Condition.LP) is Condition.LP


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10.0, Severity.SLIGHT),    # under the 20mm NM allowance
        (20.0, Severity.SLIGHT),    # exactly the NM allowance
        (50.0, Severity.MINOR),     # into the 80mm LP allowance
        (150.0, Severity.MINOR),    # into the 160mm MP allowance
        (200.0, Severity.MODERATE), # past MP, HP is unbounded
    ],
)
def test_edgewear_severity_derives_from_measurement(value, expected):
    assert severity_for_measurement("edgewear", value) is expected


def test_unknown_imperfection_is_rejected():
    with pytest.raises(KeyError):
        severity_for_measurement("sparkles", 1.0)


def test_seed_rows_cover_every_imperfection_and_condition():
    rows = seed_rows()
    assert len(rows) == len(RUBRIC) * len(Condition)
    pairs = {(r["imperfection"], r["condition"]) for r in rows}
    assert len(pairs) == len(rows), "seed rows must be unique per (imperfection, condition)"


def test_seed_rows_compute_percentage_of_card_area():
    row = next(
        r for r in seed_rows()
        if r["imperfection"] == "scuffing" and r["condition"] is Condition.NM
    )
    # 315mm² of a 5,544mm² card is the 6% the standard quotes.
    assert row["percent_of_card"] == pytest.approx(5.6818, abs=0.001)
