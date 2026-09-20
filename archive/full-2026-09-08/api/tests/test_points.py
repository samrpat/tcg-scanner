"""Grading arithmetic: points, floors, and conservative resolution."""

import pytest

from app.conditioning.points import Defect, grade, psa_estimate, total_points
from app.enums import Condition, Severity


def d(imperfection: str, severity: Severity) -> Defect:
    return Defect(imperfection=imperfection, severity=severity)


def test_no_defects_is_near_mint():
    result = grade([])
    assert result.condition is Condition.NM
    assert result.points == 0


def test_points_accumulate_across_defects():
    defects = [d("edgewear", Severity.SLIGHT), d("scuffing", Severity.SLIGHT)]
    assert total_points(defects) == 2
    assert grade(defects).condition is Condition.NM


def test_minor_edgewear_alone_is_lightly_played():
    """NM tolerates only Slight edgewear (20mm), so Minor floors the grade at LP even at
    2 points — well inside the NM ceiling of 3."""
    result = grade([d("edgewear", Severity.MINOR)])
    assert result.points == 2
    assert result.condition is Condition.LP
    assert result.limited_by == "edgewear"


def test_one_point_past_the_ceiling_drops_a_bucket():
    # 2 + 2 = 4 points, one past the NM ceiling of 3.
    result = grade([d("edgewear", Severity.MINOR), d("scratch", Severity.MINOR)])
    assert result.points == 4
    assert result.condition is Condition.LP
    assert result.limited_by == "points"


def test_a_single_bend_can_never_be_near_mint():
    """Bend is on the NM not-allowed list, so it floors the grade regardless of points."""
    result = grade([d("bend", Severity.MINOR)])
    assert result.points == 2  # comfortably inside the NM ceiling of 3
    assert result.condition is Condition.LP
    assert result.limited_by == "bend"


def test_a_fault_can_never_be_better_than_moderately_played():
    result = grade([d("fault", Severity.SLIGHT)])
    assert result.points == 1
    assert result.condition is Condition.MP
    assert result.limited_by == "fault"


def test_any_damage_is_damaged():
    result = grade([d("damage", Severity.SLIGHT)])
    assert result.condition is Condition.DMG


def test_the_worse_of_the_two_constraints_wins():
    """High points and a floor together: whichever is worse decides."""
    heavy = [d("scuffing", Severity.MAJOR), d("scratch", Severity.MAJOR)]  # 16 points -> HP
    result = grade([*heavy, d("bend", Severity.SLIGHT)])  # bend floors at LP, points say HP
    assert result.condition is Condition.HP


def test_grade_is_explainable():
    result = grade([d("edgewear", Severity.MINOR), d("grime", Severity.SLIGHT)])
    text = result.explain()
    assert "edgewear" in text
    assert "3pts" in text


def test_measured_defects_derive_severity_from_the_rubric():
    from app.conditioning.points import from_measurement

    defect = from_measurement("edgewear", 150.0)
    assert defect.severity is Severity.MINOR
    assert defect.measured_value == 150.0


def test_psa_estimate_is_a_range_not_a_number():
    low, high = psa_estimate(grade([]))
    assert (low, high) == (8, 10)
    assert low < high


@pytest.mark.parametrize(
    ("defects", "expected"),
    [
        ([("edgewear", Severity.SLIGHT)], Condition.NM),
        ([("edgewear", Severity.MINOR), ("scuffing", Severity.MINOR)], Condition.LP),
        # Moderate edgewear is only tolerated at HP (>160mm), which outweighs the 6 points.
        ([("surface_wear", Severity.MINOR), ("edgewear", Severity.MODERATE)], Condition.HP),
        ([("surface_wear", Severity.MINOR), ("edgewear", Severity.MINOR)], Condition.MP),
        ([("scuffing", Severity.MAJOR), ("indentation", Severity.MAJOR)], Condition.HP),
        ([("damage", Severity.MAJOR)], Condition.DMG),
    ],
)
def test_representative_cards(defects, expected):
    assert grade([d(i, s) for i, s in defects]).condition is expected
