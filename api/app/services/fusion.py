"""Fuse independent identification signals (TASK-020).

Registration answers "which picture is this?". That is the wrong question whenever the same
picture appears on more than one card, which happens constantly: reprints, promos, and set
reissues all share art. Measured on CARD-000020, a Gyarados: Ancient Origins scored 191 ORB
inliers and Generations scored 181 against the same capture. No feature matcher can separate
those, because the artwork genuinely is the same artwork.

The collector number can, and it is printed on every card. "20/98" belongs to a 98-card set;
Generations has 83. One number settles it.

The rule here is corroboration, never override. OCR on a photographed card is noisy — measured
on twenty real captures it read a usable number on 20/20 but got the number itself wrong on
about a fifth of them, usually by dropping a leading digit. A signal that is right most of the
time is excellent for breaking a tie and dangerous as an authority, so:

- the set total re-ranks candidates and can rescue an ambiguous match
- the card number adds a smaller bonus on top
- a confident disagreement lowers confidence and asks for review rather than picking a winner

The set total is weighted well above the card number deliberately. It is a 2-3 digit group read
from a fixed position, and it was correct on 9 of the 10 same-set cards in the sample, while the
card number varies in length and position and is the part OCR most often clips.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.imaging.ocr import CollectorNumber
from app.models import Card, CardSet

# Multipliers applied to a candidate's inlier count. Chosen so that the total alone can overturn
# the 191-vs-181 Gyarados tie (1.35 vs 1.0 is decisive under DECISIVE_MARGIN) but cannot
# manufacture a winner out of a candidate that barely registered at all.
TOTAL_MATCH_BONUS = 1.35
NUMBER_MATCH_BONUS = 1.15
# Applied when OCR read a set total confidently and the candidate's set has a different one.
MISMATCH_PENALTY = 0.75

# Below this, tesseract's own confidence is too low to act on at all.
MIN_OCR_CONFIDENCE = 0.02


@dataclass(frozen=True)
class Fusion:
    """What OCR did to the ranking, kept for the review UI and for debugging."""

    applied: bool
    reading: dict | None
    agreed: bool | None
    note: str
    # Whether the winning candidate is positively supported by the reading, as opposed to merely
    # being the one left standing. This is what decides whether a re-rank needs a human.
    corroborated: bool = False

    def as_dict(self) -> dict:
        return {
            "applied": self.applied,
            "reading": self.reading,
            "agreed": self.agreed,
            "corroborated": self.corroborated,
            "note": self.note,
        }


async def set_totals(session: AsyncSession, tcgdex_ids: list[str]) -> dict[str, int | None]:
    """Printed set total for each card, by tcgdex id.

    `card_count_official` is the number printed after the slash — the count of the main set,
    excluding secret rares, which is exactly what a card says. `card_count_total` includes them
    and is the wrong column: a 113-card set prints "/113" on its 114th card too.
    """
    if not tcgdex_ids:
        return {}
    rows = (
        await session.execute(
            select(Card.tcgdex_id, CardSet.card_count_official)
            .join(CardSet, Card.set_id == CardSet.id)
            .where(Card.tcgdex_id.in_(tcgdex_ids))
        )
    ).all()
    return {tcgdex_id: total for tcgdex_id, total in rows}


def _number_matches(reading: CollectorNumber, local_id: str | None) -> bool:
    """Does the read number match this card's printed number?

    `local_id` is a string and may be zero-padded ("086"), or carry a promo prefix ("XY48") or a
    subset marker ("TG12"), so compare on the digits.
    """
    if not local_id:
        return False
    digits = "".join(ch for ch in local_id if ch.isdigit())
    if not digits:
        return False
    return int(digits) == reading.number


def rescore(
    scored: list[tuple],
    reading: CollectorNumber | None,
    totals: dict[str, int | None],
) -> tuple[list[tuple], Fusion]:
    """Re-rank `(match, card)` pairs using a collector number reading.

    Returns the re-ranked list and a record of what happened. `scored` is not mutated.
    """
    if reading is None:
        return scored, Fusion(False, None, None, "no collector number read")
    if reading.confidence < MIN_OCR_CONFIDENCE and reading.total is None:
        return scored, Fusion(
            False, reading.as_dict(), None, "collector number read too weak to use"
        )

    ranked = []
    for match, card in scored:
        weight = 1.0
        total = totals.get(card.tcgdex_id)
        if reading.total is not None and total:
            weight *= TOTAL_MATCH_BONUS if total == reading.total else MISMATCH_PENALTY
        if _number_matches(reading, getattr(card, "local_id", None)):
            weight *= NUMBER_MATCH_BONUS
        ranked.append((match, card, match.inliers * weight))

    ranked.sort(key=lambda triple: triple[2], reverse=True)
    reordered = [(match, card) for match, card, _ in ranked]

    was, now = scored[0][1], reordered[0][1]
    # "Agreed" must mean the number actually corroborates the winner, not merely that the order
    # happened not to change. Without this distinction a wrong reading against a wrong card
    # reports agreement, which is worse than saying nothing: CARD-000014 read 35/113 against
    # Zebstrika bw1-43 and claimed the two agreed.
    corroborates = _corroborates(reading, now, totals)
    if was.tcgdex_id != now.tcgdex_id:
        agreed = False
        note = (
            f"collector number {_shown(reading)} moved {now.tcgdex_id} ahead of "
            f"{was.tcgdex_id}"
        )
    elif corroborates:
        agreed = True
        note = f"collector number {_shown(reading)} confirms {now.tcgdex_id}"
    else:
        agreed = None
        note = (
            f"collector number {_shown(reading)} matches nothing in the shortlist — "
            "the number was read but corroborates no candidate"
        )
    return reordered, Fusion(True, reading.as_dict(), agreed, note, corroborates)


def _corroborates(reading: CollectorNumber, card, totals: dict[str, int | None]) -> bool:
    """True when the reading positively supports this card, on either the total or the number."""
    total = totals.get(card.tcgdex_id)
    if reading.total is not None and total and total == reading.total:
        return True
    return _number_matches(reading, getattr(card, "local_id", None))


async def candidates_by_number(
    session: AsyncSession, reading: CollectorNumber, limit: int = 12
) -> list:
    """Cards whose printed number matches this reading, looked up directly in the catalogue.

    The perceptual hash is a similarity search over *pictures*, so it fails exactly where the
    picture is hard to hash: reverse holos, heavy glare, a foil pattern that swamps the art.
    CARD-000014 is one — a reverse-holo Empoleon whose true match never entered the shortlist,
    so no amount of re-ranking could reach it, while OCR read its "35/113" perfectly.

    A number and a set total are close to a primary key. "35/113" narrows 23,544 cards to two
    (Empoleon in Legendary Treasures and Ditto in Delta Species), which registration then tells
    apart trivially. This turns OCR from a tie-breaker into a way back in.

    Returns Candidates with distance 98 — worse than any real hash distance, so they never
    outrank a genuine prefilter hit on the strength of this lookup alone. They still have to
    register.
    """
    from app.services.art_index import Candidate

    if reading.total is None:
        return []
    digits = str(reading.number)
    rows = (
        await session.execute(
            select(
                Card.tcgdex_id,
                Card.name,
                CardSet.name,
                Card.image_url,
                Card.local_id,
            )
            .join(CardSet, Card.set_id == CardSet.id)
            .where(
                CardSet.card_count_official == reading.total,
                Card.image_url.is_not(None),
                # local_id is a string and may be zero-padded, so match both forms rather than
                # casting: "35" and "035" are the same card.
                Card.local_id.in_([digits, digits.zfill(2), digits.zfill(3)]),
            )
            .limit(limit)
        )
    ).all()
    return [
        Candidate(
            tcgdex_id=r[0],
            name=r[1],
            set_name=r[2],
            image_url=r[3],
            local_id=r[4],
            distance=98,
        )
        for r in rows
    ]


def _shown(reading: CollectorNumber) -> str:
    if reading.prefix:
        return f"{reading.prefix}{reading.number}"
    return f"{reading.number}/{reading.total}" if reading.total else str(reading.number)


def adjusted_weight(
    card, reading: CollectorNumber | None, totals: dict[str, int | None]
) -> float:
    """The multiplier `rescore` would apply to one card. Exposed for tests and diagnostics."""
    if reading is None:
        return 1.0
    weight = 1.0
    total = totals.get(card.tcgdex_id)
    if reading.total is not None and total:
        weight *= TOTAL_MATCH_BONUS if total == reading.total else MISMATCH_PENALTY
    if _number_matches(reading, getattr(card, "local_id", None)):
        weight *= NUMBER_MATCH_BONUS
    return weight
