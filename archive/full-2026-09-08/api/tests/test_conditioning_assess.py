"""Persisting a human's condition assessment."""

from app.conditioning.points import Defect, grade
from app.enums import Condition, Severity


def test_no_defects_is_near_mint():
    result = grade([])
    assert result.condition is Condition.NM
    assert result.points == 0


def test_points_sum_and_pick_the_bucket():
    """The grade is arithmetic, not judgement (D-003): NM<=3, LP<=6, MP<=12, HP<=24."""
    result = grade(
        [
            Defect(imperfection="edgewear", severity=Severity.MINOR),
            Defect(imperfection="scuffing", severity=Severity.SLIGHT),
        ]
    )
    assert result.points == 3
    # 3 points is inside the NM ceiling, so anything worse than NM must come from a
    # per-imperfection allowance rather than the total.
    assert result.condition is Condition.LP
    assert result.limited_by == "edgewear"


def test_explanation_shows_the_sum():
    """A grade a buyer disputes has to be explainable, so the arithmetic is spelled out."""
    result = grade([Defect(imperfection="edgewear", severity=Severity.MINOR)])
    text = result.explain()
    assert "edgewear" in text
    assert "2pt" in text
    assert result.condition.value in text


def test_severity_escalates_the_grade():
    previous = -1
    for severity in (Severity.SLIGHT, Severity.MINOR, Severity.MODERATE, Severity.MAJOR):
        points = grade([Defect(imperfection="scratch", severity=severity)]).points
        assert points > previous
        previous = points


def test_a_major_defect_alone_can_exceed_near_mint():
    result = grade([Defect(imperfection="damage", severity=Severity.MAJOR)])
    assert result.condition is not Condition.NM
