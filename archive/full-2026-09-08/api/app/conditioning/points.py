"""Turn observed imperfections into a condition.

Two independent constraints, and the worse one wins:

  1. the point total against the ceilings (NM 3, LP 6, MP 12, HP 24)
  2. the per-imperfection allowances, which implement the "Not Allowed" lists

That second constraint is why a single Bend can never be Near Mint no matter how few points
the card has accumulated.
"""

from dataclasses import dataclass, field

from app.conditioning.rubric import (
    SEVERITY_POINTS,
    best_condition_for,
    condition_for_points,
    severity_for_measurement,
    worse_of,
)
from app.enums import Condition, Severity


@dataclass(frozen=True)
class Defect:
    """One observed imperfection.

    `measured_value` is optional: the Phase 4 manual picker supplies a severity directly,
    while Phase 11 measurement supplies a value and lets the rubric derive the severity.
    """

    imperfection: str
    severity: Severity
    measured_value: float | None = None
    face: str | None = None  # "front" | "back"
    note: str | None = None

    @property
    def points(self) -> int:
        return SEVERITY_POINTS[self.severity]

    def to_dict(self) -> dict:
        return {
            "imperfection": self.imperfection,
            "severity": self.severity.value,
            "points": self.points,
            "measured_value": self.measured_value,
            "face": self.face,
            "note": self.note,
        }


@dataclass(frozen=True)
class Grade:
    condition: Condition
    points: int
    defects: list[Defect] = field(default_factory=list)
    # Which constraint decided it — shown in the UI so a grade can always be explained.
    limited_by: str = "points"

    def explain(self) -> str:
        if not self.defects:
            return "No imperfections recorded — Near Mint."
        parts = [
            f"{d.imperfection.replace('_', ' ')} ({d.severity.value}, {d.points}pt)"
            for d in self.defects
        ]
        basis = (
            "point total" if self.limited_by == "points" else f"{self.limited_by} allowance"
        )
        detail = " + ".join(parts)
        return f"{self.condition.value}: {detail} = {self.points}pts, limited by {basis}."

    def to_dict(self) -> dict:
        return {
            "condition": self.condition.value,
            "points": self.points,
            "limited_by": self.limited_by,
            "defects": [d.to_dict() for d in self.defects],
            "explanation": self.explain(),
        }


def from_measurement(imperfection: str, value: float, face: str | None = None) -> Defect:
    """Build a defect from a measured length/area/lift, deriving severity from the rubric."""
    return Defect(
        imperfection=imperfection,
        severity=severity_for_measurement(imperfection, value),
        measured_value=value,
        face=face,
    )


def total_points(defects: list[Defect]) -> int:
    return sum(d.points for d in defects)


def grade(defects: list[Defect]) -> Grade:
    """Grade a card conservatively. An empty defect list is Near Mint."""
    points = total_points(defects)
    condition = condition_for_points(points)
    limited_by = "points"

    for defect in defects:
        allowed = best_condition_for(defect.imperfection, defect.severity)
        worse = worse_of(condition, allowed)
        if worse is not condition:
            condition = worse
            limited_by = defect.imperfection

    return Grade(condition=condition, points=points, defects=list(defects), limited_by=limited_by)


def psa_estimate(g: Grade) -> tuple[int, int]:
    """A rough PSA band, shown only as a secondary badge (D-003).

    Deliberately a range, never a single number, and never used to set a sale condition:
    PSA weights centering heavily and the raw standard ignores it up to 70/30.
    """
    bands: dict[Condition, tuple[int, int]] = {
        Condition.NM: (8, 10),
        Condition.LP: (6, 8),
        Condition.MP: (4, 6),
        Condition.HP: (2, 4),
        Condition.DMG: (1, 2),
    }
    return bands[g.condition]
