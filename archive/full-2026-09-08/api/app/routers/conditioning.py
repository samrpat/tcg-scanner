"""Exposes the grading rubric so the Phase 4 manual picker can be data-driven."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.conditioning.points import Defect, grade, psa_estimate
from app.conditioning.rubric import (
    CARD_AREA_MM2,
    CARD_LENGTH_MM,
    CARD_WIDTH_MM,
    CONDITION_CEILINGS,
    IMPERFECTION_LABELS,
    RUBRIC,
    SEVERITY_POINTS,
)
from app.conditioning.translate import DEFAULT_TRANSLATIONS
from app.db import get_session
from app.enums import (
    AssessmentSource,
    Condition,
    InventoryStatus,
    Marketplace,
    ReviewCategory,
    ReviewStatus,
    Severity,
)
from app.models import ConditionAssessment, InventoryItem, Review, User
from app.routers.capture import current_user

router = APIRouter(prefix="/conditioning", tags=["conditioning"])


class DefectIn(BaseModel):
    imperfection: str = Field(examples=["edgewear"])
    severity: Severity = Field(examples=[Severity.MINOR])
    measured_value: float | None = None
    face: str | None = None

    @field_validator("imperfection")
    @classmethod
    def known_imperfection(cls, value: str) -> str:
        """Reject unknown imperfections at the boundary, so the rubric never sees one.

        Without this the KeyError from `best_condition_for` surfaces as a 500 and a stack
        trace instead of a 422 naming the valid options.
        """
        if value not in RUBRIC:
            raise ValueError(f"unknown imperfection {value!r}; expected one of {sorted(RUBRIC)}")
        return value


class GradeIn(BaseModel):
    defects: list[DefectIn] = []


@router.get("/rubric")
async def get_rubric() -> dict:
    return {
        "card": {
            "length_mm": CARD_LENGTH_MM,
            "width_mm": CARD_WIDTH_MM,
            "area_mm2": CARD_AREA_MM2,
        },
        "severity_points": {s.value: p for s, p in SEVERITY_POINTS.items()},
        "ceilings": {c.value: p for c, p in CONDITION_CEILINGS.items()},
        "imperfections": [
            {
                "key": key,
                "label": IMPERFECTION_LABELS.get(key, key),
                "measure": measure,
                "thresholds": {
                    condition.value: {
                        "severity": t.severity.value if t.severity else None,
                        "max_value": t.max_value,
                        "disallowed": t.disallowed,
                        "note": t.note,
                    }
                    for condition, t in thresholds.items()
                },
            }
            for key, (measure, thresholds) in RUBRIC.items()
        ],
    }


@router.post("/grade")
async def grade_defects(body: GradeIn) -> dict:
    """Grade a defect list. Pure function — no card or inventory item required."""
    defects = [
        Defect(
            imperfection=d.imperfection,
            severity=d.severity,
            measured_value=d.measured_value,
            face=d.face,
        )
        for d in body.defects
    ]
    result = grade(defects)
    low, high = psa_estimate(result)
    return {
        **result.to_dict(),
        "psa_estimate": {"low": low, "high": high, "note": "Secondary only; never sets condition."},
        "marketplace": {
            marketplace.value: {
                "code": mapping[result.condition].external_code,
                "label": mapping[result.condition].external_label,
                "requires_photo": mapping[result.condition].requires_photo,
            }
            for marketplace, mapping in DEFAULT_TRANSLATIONS.items()
        },
    }


class AssessIn(BaseModel):
    defects: list[DefectIn] = []
    note: str | None = None


class ConditionIn(BaseModel):
    """A condition stated directly, without going through the points rubric."""

    condition: Condition


@router.post("/{sku}/condition")
async def set_condition(
    sku: str,
    body: ConditionIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Set a card's condition directly.

    The rubric route — enumerate imperfections, sum points, let the arithmetic pick the bucket —
    is still there and is the more defensible way to arrive at a grade. But the operator grading
    their own collection already knows what "Lightly Played" means and does not need to be walked
    to it through eleven checkboxes, and a slow path is one that gets skipped or rubber-stamped.
    Stating the condition is one click, and it can be changed at any point up to listing.

    Recorded as an assessment like any other, so the append-only history still holds who said
    what and when.
    """
    item = await _item(session, sku, user)

    await session.execute(
        update(ConditionAssessment)
        .where(
            ConditionAssessment.inventory_item_id == item.id,
            ConditionAssessment.is_accepted.is_(True),
        )
        .values(is_accepted=False)
    )
    session.add(
        ConditionAssessment(
            inventory_item_id=item.id,
            source=AssessmentSource.HUMAN,
            condition=body.condition,
            points_total=None,
            defects=None,
            confidence=1.0,
            is_accepted=True,
        )
    )
    item.condition = body.condition
    item.condition_points = None
    if item.status is InventoryStatus.IDENTIFIED:
        item.status = InventoryStatus.GRADED
    await session.commit()
    return {"ok": True, "sku": sku, "condition": body.condition.value}


@router.get("/{sku}/assessment")
async def get_assessment(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """The accepted assessment for a card, if it has one."""
    item = await _item(session, sku, user)
    accepted = (
        await session.execute(
            select(ConditionAssessment)
            .where(
                ConditionAssessment.inventory_item_id == item.id,
                ConditionAssessment.is_accepted.is_(True),
            )
            .order_by(desc(ConditionAssessment.created_at))
        )
    ).scalars().first()

    history = (
        (
            await session.execute(
                select(ConditionAssessment)
                .where(ConditionAssessment.inventory_item_id == item.id)
                .order_by(desc(ConditionAssessment.created_at))
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return {
        "sku": sku,
        "condition": item.condition.value if item.condition else None,
        "points": item.condition_points,
        "accepted": _assessment_dict(accepted) if accepted else None,
        "history": [_assessment_dict(a) for a in history],
    }


@router.post("/{sku}/assess")
async def assess(
    sku: str,
    body: AssessIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Record a human's condition assessment for one card.

    The grade itself is arithmetic (D-003): the operator says what they can see and how bad it
    is, and the rubric decides the condition. Nobody picks "Lightly Played" from a dropdown,
    because that is the judgement call the points system exists to remove.

    Assessments are append-only — an earlier one is never edited or deleted, it is just no
    longer the accepted row. That matters once Phase 11 adds machine assessments: the record of
    what the model said and what the human said afterwards is the only way to tell whether the
    model is any good.
    """
    item = await _item(session, sku, user)

    defects = [
        Defect(
            imperfection=d.imperfection,
            severity=d.severity,
            measured_value=d.measured_value,
            face=d.face,
        )
        for d in body.defects
    ]
    result = grade(defects)

    # Demote whatever was accepted before rather than deleting it.
    await session.execute(
        update(ConditionAssessment)
        .where(
            ConditionAssessment.inventory_item_id == item.id,
            ConditionAssessment.is_accepted.is_(True),
        )
        .values(is_accepted=False)
    )

    assessment = ConditionAssessment(
        inventory_item_id=item.id,
        source=AssessmentSource.HUMAN,
        condition=result.condition,
        points_total=result.points,
        defects=result.to_dict().get("defects", []),
        # A person looking at the card is the ground truth this system grades against.
        confidence=1.0,
        is_accepted=True,
    )
    session.add(assessment)

    item.condition = result.condition
    item.condition_points = result.points
    if item.status is InventoryStatus.IDENTIFIED:
        item.status = InventoryStatus.GRADED

    # Close any open condition review: it has just been answered.
    open_review = (
        await session.execute(
            select(Review).where(
                Review.inventory_item_id == item.id,
                Review.category == ReviewCategory.CONDITION,
                Review.status == ReviewStatus.OPEN,
            )
        )
    ).scalar_one_or_none()
    if open_review is not None:
        open_review.status = ReviewStatus.RESOLVED
        open_review.resolved_at = datetime.now(UTC)
        open_review.resolution = {"resolved_by": "operator", "note": body.note}

    await session.commit()
    return {
        "ok": True,
        "sku": sku,
        "condition": result.condition.value,
        "points": result.points,
        "limited_by": result.limited_by,
        "explanation": result.explain(),
    }


async def _item(session: AsyncSession, sku: str, user: User) -> InventoryItem:
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.user_id == user.id, InventoryItem.sku == sku
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"no such card: {sku}")
    return item


def _assessment_dict(a: ConditionAssessment) -> dict:
    return {
        "id": str(a.id),
        "source": a.source.value,
        "condition": a.condition.value,
        "points": a.points_total,
        "defects": a.defects or [],
        "accepted": a.is_accepted,
        "created_at": a.created_at.isoformat(),
    }


@router.post("/{sku}/condition/clear")
async def clear_condition(
    sku: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Unset a card's condition.

    The assessment rows are not deleted — the table is append-only — the accepted one is simply
    demoted, so the record of what was once claimed survives.
    """
    item = await _item(session, sku, user)
    await session.execute(
        update(ConditionAssessment)
        .where(
            ConditionAssessment.inventory_item_id == item.id,
            ConditionAssessment.is_accepted.is_(True),
        )
        .values(is_accepted=False)
    )
    item.condition = None
    item.condition_points = None
    await session.commit()
    return {"ok": True, "sku": sku, "condition": None}


@router.get("/translations")
async def translations() -> dict:
    return {
        marketplace.value: {
            condition.value: {
                "code": t.external_code,
                "label": t.external_label,
                "requires_photo": t.requires_photo,
                "note": t.note,
            }
            for condition, t in mapping.items()
        }
        for marketplace, mapping in DEFAULT_TRANSLATIONS.items()
    }


@router.get("/conditions")
async def conditions() -> list[dict]:
    return [
        {"value": c.value, "ceiling": CONDITION_CEILINGS.get(c)} for c in Condition
    ]


@router.get("/marketplaces")
async def marketplaces() -> list[str]:
    return [m.value for m in Marketplace]
