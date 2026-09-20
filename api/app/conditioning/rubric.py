"""The TCGplayer conditioning rubric as data.

Source: TCGplayer Card Conditioning Standards, March 2025. See docs/CONDITION.md.
This module is pure data and pure functions — no database, no I/O — so it is trivially testable
and can be reused by the Phase 11 automated measurement code unchanged.
"""

from dataclasses import dataclass

from app.enums import Condition, Severity

# --- Card geometry (Pokémon / Magic standard size) -------------------------------------------
# Every threshold below is expressed against these. Phase 2 dewarps to exactly this rectangle,
# which is what makes pixels-to-millimetres deterministic (D-007).
CARD_LENGTH_MM = 88.0
CARD_WIDTH_MM = 63.0
CARD_AREA_MM2 = 5544.0
CARD_BORDER_MM = 302.0

# Oversized cards are 1.4x, and the standard allows 1.4x the imperfection size to match.
OVERSIZED_SCALE = 1.4

# --- Points ----------------------------------------------------------------------------------
SEVERITY_POINTS: dict[Severity, int] = {
    Severity.SLIGHT: 1,
    Severity.MINOR: 2,
    Severity.MODERATE: 4,
    Severity.MAJOR: 8,
}

CONDITION_CEILINGS: dict[Condition, int] = {
    Condition.NM: 3,
    Condition.LP: 6,
    Condition.MP: 12,
    Condition.HP: 24,
}

# Best to worst. Index order is meaningful — `worse_of` relies on it.
CONDITION_ORDER: list[Condition] = [
    Condition.NM,
    Condition.LP,
    Condition.MP,
    Condition.HP,
    Condition.DMG,
]

# --- Measurement kinds -------------------------------------------------------------------------
LENGTH = "length"  # millimetres along the card or its border
AREA = "area"      # square millimetres
LIFT = "lift"      # millimetres of lift from a flat surface
NONE = "none"      # not permitted at any measurement
ANY = "any"        # permitted without limit


@dataclass(frozen=True)
class Threshold:
    """What a single imperfection is allowed to be at a single condition."""

    severity: Severity | None
    max_value: float | None  # None with severity set means "unbounded at this condition"
    disallowed: bool = False
    note: str | None = None


# imperfection -> (measure, {condition: Threshold})
#
# `disallowed=True` means the imperfection may not appear at all at that condition, which is
# what implements the "Not Allowed" lists on pages 7-8 of the standard.
RUBRIC: dict[str, tuple[str, dict[Condition, Threshold]]] = {
    "edgewear": (
        LENGTH,
        {
            Condition.NM: Threshold(Severity.SLIGHT, 20.0),
            Condition.LP: Threshold(Severity.MINOR, 80.0),
            Condition.MP: Threshold(Severity.MINOR, 160.0),
            Condition.HP: Threshold(Severity.MODERATE, None),
            Condition.DMG: Threshold(Severity.MAJOR, None),
        },
    ),
    "surface_wear": (
        AREA,
        {
            Condition.NM: Threshold(None, None, disallowed=True),
            Condition.LP: Threshold(Severity.SLIGHT, 2.5),
            Condition.MP: Threshold(Severity.MINOR, 10.0),
            Condition.HP: Threshold(Severity.MODERATE, 40.0),
            Condition.DMG: Threshold(Severity.MAJOR, None),
        },
    ),
    "scratch": (
        LENGTH,
        {
            Condition.NM: Threshold(Severity.MINOR, 40.0),
            Condition.LP: Threshold(Severity.MINOR, 40.0),
            Condition.MP: Threshold(Severity.MODERATE, None),
            Condition.HP: Threshold(Severity.MAJOR, None),
            Condition.DMG: Threshold(Severity.MAJOR, None),
        },
    ),
    "scuffing": (
        AREA,
        {
            Condition.NM: Threshold(Severity.SLIGHT, 315.0),
            Condition.LP: Threshold(Severity.MINOR, 2772.0),
            Condition.MP: Threshold(
                Severity.MODERATE,
                5544.0,
                note="Total coverage; no clear patch above 5mm². Partial counts as 2x minor.",
            ),
            Condition.HP: Threshold(
                Severity.MAJOR, 11088.0, note="Both faces combined."
            ),
            Condition.DMG: Threshold(Severity.MAJOR, None),
        },
    ),
    "indentation": (
        AREA,
        {
            Condition.NM: Threshold(
                Severity.SLIGHT, 0.055, note="One pinpoint. Must not show through the back."
            ),
            Condition.LP: Threshold(
                Severity.MINOR, 4.0, note="Must not show through the back."
            ),
            Condition.MP: Threshold(Severity.MODERATE, 25.0),
            Condition.HP: Threshold(Severity.MAJOR, None),
            Condition.DMG: Threshold(Severity.MAJOR, None),
        },
    ),
    "bend": (
        LENGTH,
        {
            Condition.NM: Threshold(None, None, disallowed=True),
            Condition.LP: Threshold(Severity.MINOR, 10.0),
            Condition.MP: Threshold(Severity.MODERATE, 20.0),
            Condition.HP: Threshold(Severity.MAJOR, None),
            Condition.DMG: Threshold(Severity.MAJOR, None),
        },
    ),
    "grime": (
        AREA,
        {
            Condition.NM: Threshold(None, None, disallowed=True),
            Condition.LP: Threshold(Severity.SLIGHT, 2.5),
            Condition.MP: Threshold(Severity.MODERATE, 277.5),
            Condition.HP: Threshold(Severity.MAJOR, None),
            Condition.DMG: Threshold(Severity.MAJOR, None),
        },
    ),
    "fault": (
        AREA,
        {
            Condition.NM: Threshold(None, None, disallowed=True),
            Condition.LP: Threshold(None, None, disallowed=True),
            Condition.MP: Threshold(Severity.SLIGHT, 2.5),
            Condition.HP: Threshold(Severity.MINOR, 10.0),
            Condition.DMG: Threshold(Severity.MODERATE, 40.0),
        },
    ),
    "defect": (
        AREA,
        {
            Condition.NM: Threshold(Severity.SLIGHT, 2.5),
            Condition.LP: Threshold(Severity.MINOR, 5.0),
            Condition.MP: Threshold(Severity.MINOR, 5.0),
            Condition.HP: Threshold(Severity.MODERATE, 10.0),
            Condition.DMG: Threshold(Severity.MAJOR, None),
        },
    ),
    "curling": (
        LIFT,
        {
            Condition.NM: Threshold(Severity.SLIGHT, 5.0),
            Condition.LP: Threshold(Severity.SLIGHT, 5.0),
            Condition.MP: Threshold(Severity.SLIGHT, 5.0),
            Condition.HP: Threshold(Severity.SLIGHT, 5.0),
            Condition.DMG: Threshold(Severity.MAJOR, None, note="Any lift beyond 5mm."),
        },
    ),
    "damage": (
        AREA,
        {
            Condition.NM: Threshold(None, None, disallowed=True),
            Condition.LP: Threshold(None, None, disallowed=True),
            Condition.MP: Threshold(None, None, disallowed=True),
            Condition.HP: Threshold(None, None, disallowed=True),
            Condition.DMG: Threshold(Severity.MAJOR, None),
        },
    ),
}

IMPERFECTIONS: list[str] = list(RUBRIC)

# Human-facing copy for the Phase 4 manual picker.
IMPERFECTION_LABELS: dict[str, str] = {
    "edgewear": "Edgewear — whitening or fraying along edges, borders or corners",
    "surface_wear": "Surface wear — material loss showing inner layers through the print",
    "scratch": "Scratch — a score that removes material",
    "scuffing": "Scuffing — a group of scratches, clouding or sleeve gloss",
    "indentation": "Indentation — a ding or dent that displaces material",
    "bend": "Bend — a line or ridge from folding or pressing",
    "grime": "Grime — dirt or smudging from handling or storage",
    "fault": "Fault — splitting, rippling, peeling or delamination",
    "defect": "Defect — a printing or manufacturing error",
    "curling": "Curling — lift from a flat surface, measured as a gap",
    "damage": "Damage — tear, hole, liquid exposure or missing material",
}


def worse_of(a: Condition, b: Condition) -> Condition:
    """Return whichever condition is worse. Conservative resolution throughout (spec §15)."""
    return a if CONDITION_ORDER.index(a) >= CONDITION_ORDER.index(b) else b


def condition_for_points(points: int) -> Condition:
    """Lowest condition whose ceiling the point total fits under."""
    for condition in CONDITION_ORDER[:-1]:
        if points <= CONDITION_CEILINGS[condition]:
            return condition
    return Condition.DMG


def severity_for_measurement(imperfection: str, value: float) -> Severity:
    """Smallest severity whose threshold covers this measurement.

    Used by Phase 11 to turn a measured region into a severity. A value that exceeds every
    bounded threshold is Major.
    """
    if imperfection not in RUBRIC:
        raise KeyError(f"unknown imperfection: {imperfection}")
    _, thresholds = RUBRIC[imperfection]
    for condition in CONDITION_ORDER:
        threshold = thresholds[condition]
        if threshold.disallowed or threshold.severity is None:
            continue
        if threshold.max_value is None or value <= threshold.max_value:
            return threshold.severity
    return Severity.MAJOR


def best_condition_for(imperfection: str, severity: Severity) -> Condition:
    """Best condition that tolerates this imperfection at this severity.

    This is what enforces the standard's "Not Allowed" lists: a Bend can never be NM, a Fault
    can never be NM or LP, and any Damage is DMG.
    """
    if imperfection not in RUBRIC:
        raise KeyError(f"unknown imperfection: {imperfection}")
    _, thresholds = RUBRIC[imperfection]
    wanted = SEVERITY_POINTS[severity]
    for condition in CONDITION_ORDER:
        threshold = thresholds[condition]
        if threshold.disallowed or threshold.severity is None:
            continue
        if SEVERITY_POINTS[threshold.severity] >= wanted:
            return condition
    return Condition.DMG


def seed_rows() -> list[dict]:
    """Flatten the rubric for the `condition_rubric` table."""
    rows: list[dict] = []
    for imperfection, (measure, thresholds) in RUBRIC.items():
        for condition, threshold in thresholds.items():
            percent = None
            if threshold.max_value is not None:
                if measure == AREA:
                    percent = round(threshold.max_value / CARD_AREA_MM2 * 100, 4)
                elif measure == LENGTH:
                    base = CARD_BORDER_MM if imperfection == "edgewear" else CARD_LENGTH_MM
                    percent = round(threshold.max_value / base * 100, 4)
            rows.append(
                {
                    "imperfection": imperfection,
                    "condition": condition,
                    "severity": threshold.severity,
                    "measure": NONE if threshold.disallowed else measure,
                    "max_value": threshold.max_value,
                    "percent_of_card": percent,
                    "disallowed": threshold.disallowed,
                    "note": threshold.note,
                }
            )
    return rows
