"""Identify a captured card: hash prefilter, then verify by registering against real art.

The two stages do different jobs and neither is sufficient alone.

A perceptual hash is cheap enough to run against the whole catalogue, but a *photograph* of a
card differs from the studio scan by lighting, glare, white balance and a foil pattern that the
scan does not have. Measured on real captures: a correct match sits at Hamming distance 2-14
while wrong cards sit at 10-18. Those bands overlap, so the hash can rank but cannot decide.

Registration decides. Matching hundreds of ORB features between the capture and a candidate's
official art either finds a consistent homography or it does not, and the inlier count separates
right from wrong by an order of magnitude rather than a few bits.
"""

import time
from dataclasses import dataclass, field

import cv2
import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.imaging.ocr import read_collector_number
from app.imaging.register import Match, register
from app.logging_setup import get_logger
from app.models import Card, CardSet
from app.services import art_cache
from app.services.art_index import nearest, nearest_by_grid
from app.services.fusion import (
    adjusted_weight,
    candidates_by_number,
    rescore,
    set_totals,
)
from app.services.hashing import grid_phash, phash

log = get_logger(__name__)

# How many prefilter candidates get the expensive registration check.
#
# Twenty was sized for the single perceptual hash, which ranked the correct card first on only
# 7 of 12 real captures and pushed two outside the top 20 entirely. The grid hash ranks all
# twelve first across 21,775 cards, so the shortlist exists to absorb the occasional near-miss
# rather than to compensate for a weak prefilter.
#
# Measured on the same twelve: 20 candidates took 7.9s per card, 6 took 1.1s, and 4 took 0.86s
# — all of them 12/12. Eight keeps a margin over the smallest that worked while costing a
# seventh of the original. The cost is almost entirely fetching candidate art over the network.
VERIFY_CANDIDATES = 8

# Registration thresholds, deliberately lower than the card-back matcher's. Card art is smaller,
# busier and partly obscured by foil, so a true match yields fewer clean features than a back.
MIN_INLIERS = 12
MIN_INLIER_RATIO = 0.22

# A match is only accepted outright when it is clearly ahead of the runner-up. Two cards that
# register almost equally well are usually the same artwork reprinted across sets, which is a
# question for a human, not a coin flip.
DECISIVE_MARGIN = 1.6

ART_QUALITY = "high"

# When the hash prefilter finds nothing that registers, fall back to the sets this user has
# been scanning. Cards arrive in batches from the same set far more often than not — nine of
# the first ten captures were one set — so it is a strong, cheap prior. It also rescues exactly
# the case the hash cannot handle: a crop that caught only part of the card, whose hash bears no
# relation to the full art but which still registers against it with eighty inliers.
SET_PRIOR_LIMIT = 120

# Hard ceiling on how long a single identification may spend before giving up and asking a
# human. Recognition runs unattended on a Pi while the operator keeps scanning, so an
# identification that grinds is worse than one that admits defeat: the queue backs up behind it
# and the last-scanned card sits unnamed on screen.
#
# Measured here: one unidentifiable card spent 137 s, almost all of it in the set-prior fallback
# re-downloading art. With the art cache that collapses on a second pass, but a first pass over
# an unseen set is still unbounded without this.
IDENTIFY_BUDGET_S = 25.0

# Per-request timeout for a single art fetch, and how many consecutive failures end the attempt.
#
# TCGdex went unreachable from this machine after the set-prior fallback issued several hundred
# requests per unidentified card — almost certainly rate limiting, and entirely self-inflicted.
# The failure mode was the worst kind: every one of 94 fetches sat waiting for a 20 s connect
# timeout, so a card took 93 s to arrive at "not identified" rather than 2 s.
#
# A short timeout plus a circuit breaker turns an outage into a fast, honest failure. Recognition
# is allowed to give up and ask a human; it is not allowed to hold the queue for a minute and a
# half while the network is down.
# When registration is this far clear of the runner-up, and this strong outright, the collector
# number cannot change the outcome and is not read. Both conditions are required: a large margin
# over a weak field still deserves the check.
#
# 2.5 sits well above DECISIVE_MARGIN (1.6) on purpose. Skipping OCR gives up a corroborating
# signal, so the bar for skipping is higher than the bar for accepting.
OCR_SKIP_MARGIN = 2.5
OCR_SKIP_MIN_INLIERS = 80

ART_TIMEOUT_S = 6.0
ART_FAILURE_CEILING = 8


@dataclass
class Identification:
    card_tcgdex_id: str | None = None
    name: str | None = None
    set_name: str | None = None
    confidence: float = 0.0
    method: str = "none"
    inliers: int = 0
    hash_distance: int | None = None
    rotation_deg: float = 0.0
    homography: np.ndarray | None = None
    candidates: list[dict] = field(default_factory=list)
    fusion: dict | None = None
    needs_review: bool = True
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "card": self.card_tcgdex_id,
            "name": self.name,
            "set": self.set_name,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "inliers": self.inliers,
            "hash_distance": self.hash_distance,
            "rotation_deg": round(self.rotation_deg, 1),
            "needs_review": self.needs_review,
            "fusion": self.fusion,
            "reason": self.reason,
            "candidates": self.candidates[:5],
        }


def _within_budget(started: float) -> bool:
    """Is there time left to try another, more expensive, way of identifying this card?

    Checked before every fallback stage rather than only the most expensive one. Each stage was
    individually reasonable and together they were not: with the art source unreachable, one card
    spent 93 s walking the whole chain to reach "not identified". Recognition runs unattended
    while the operator keeps scanning, so a slow failure blocks the queue behind it and leaves
    the last card on screen unnamed — which is exactly what was reported.
    """
    return (time.monotonic() - started) < IDENTIFY_BUDGET_S


class _Breaker:
    """Stops a whole identification hammering a source that is plainly not answering.

    Consecutive failures only: a handful of genuinely missing images scattered through a run is
    normal and must not trip it, whereas eight failures in a row means the source is down.
    """

    __slots__ = ("failures",)

    def __init__(self) -> None:
        self.failures = 0

    @property
    def tripped(self) -> bool:
        return self.failures >= ART_FAILURE_CEILING

    def record_failure(self) -> None:
        self.failures += 1

    def record_success(self) -> None:
        self.failures = 0


async def _fetch_art(
    client: httpx.AsyncClient, url: str, breaker: "_Breaker | None" = None
) -> np.ndarray | None:
    """Reference art for a card, from disk if it has ever been fetched before.

    Card art is immutable, so the correct number of downloads per card is one. Without the cache
    a failed identification re-fetched hundreds of images and took 137 s; on a Pi that is the
    difference between usable and not.
    """
    full = f"{url}/{ART_QUALITY}.jpg"
    cached = art_cache.get(full)
    if cached is not None:
        return cached
    if breaker is not None and breaker.tripped:
        return None
    try:
        response = await client.get(full)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - one missing image must not sink the run
        if breaker is not None:
            breaker.record_failure()
        log.warning(
            "recognition.art_failed", url=url, error=str(exc) or type(exc).__name__
        )
        return None
    if breaker is not None:
        breaker.record_success()
    art_cache.put(full, response.content)
    buffer = np.frombuffer(response.content, dtype=np.uint8)
    art = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    return art if art is not None and art.size else None


async def identify(
    session: AsyncSession,
    capture: np.ndarray,
    *,
    candidates: int = VERIFY_CANDIDATES,
    fallback: np.ndarray | None = None,
) -> Identification:
    """Identify a rectified capture.

    `fallback` is the original, uncropped photograph. Registration does not need a good crop —
    it finds the art wherever it sits in the frame — so when the rectified image fails to
    identify, the original gets a turn. That breaks a genuine chicken-and-egg: a bad crop
    caused a recognition failure, while recognition is exactly what would have fixed the crop.
    """
    started = time.monotonic()

    # Fast path first: one hash, twenty candidates. This resolves the overwhelming majority in
    # around two seconds. The wider searches below cost several times that and are only worth
    # paying for when the cheap path has actually failed.
    # Grid hash, not the single perceptual hash. A 64-bit hash averages the whole card and so
    # loses layout, which is most of what tells cards apart: it put the correct card outside the
    # top 20 on 2 of 12 real captures, where registration would never have seen it. The grid
    # hash ranks all twelve first across 21,775 cards in about 40ms.
    shortlist = await nearest_by_grid(session, grid_phash(capture), limit=candidates)
    if not shortlist:
        shortlist = await nearest(session, phash(capture), limit=candidates)
    if not shortlist:
        return Identification(reason="catalogue has no hashed art")

    # Art is fetched for the whole shortlist at once. Serially this was the dominant cost —
    # twenty round trips to a public CDN at four to six seconds a card, against a 36s budget
    # for the entire capture-to-listing loop.
    import asyncio

    usable = [c for c in shortlist if c.image_url]
    breaker = _Breaker()
    async with httpx.AsyncClient(timeout=ART_TIMEOUT_S, follow_redirects=True) as client:
        arts = await asyncio.gather(
            *(_fetch_art(client, c.image_url, breaker) for c in usable)
        )

    scored: list[tuple[Match, object]] = []
    for candidate, art in zip(usable, arts, strict=True):
        if art is None:
            continue
        match = register(capture, art, min_inliers=MIN_INLIERS, min_inlier_ratio=MIN_INLIER_RATIO)
        if match is not None:
            scored.append((match, candidate))

    result = Identification(
        hash_distance=shortlist[0].distance,
        candidates=[c.as_dict() for c in shortlist],
    )

    if not scored:
        # The cheap path found nothing. Widen, in increasing order of cost.
        #
        # First: the hash at four right-angle rotations. A perceptual hash is not rotation
        # invariant, so a capture that came out sideways ranks its own card nowhere at all.
        rotated = await _nearest_any_orientation(session, capture, candidates)
        fresh = [c for c in rotated if c.tcgdex_id not in {x.tcgdex_id for x in shortlist}]
        if fresh and _within_budget(started):
            scored = await _register_all(capture, fresh)
            if scored:
                result.method = "registration-rotated"

        # Then: the number printed on the card. This runs before the set prior because it is
        # both cheaper and sharper — a number and set total narrow 23,544 cards to a handful,
        # where the prior fetches art for up to SET_PRIOR_LIMIT cards to find one. Measured
        # while auto-recognition was running over twenty cards, the prior path took 63 s per
        # unidentified card and issued hundreds of requests to TCGdex; the number lookup
        # answers the same question in a single indexed query plus a few fetches.
        if not scored and _within_budget(started):
            early_reading = read_collector_number(capture)
            if early_reading is not None:
                by_number = await candidates_by_number(session, early_reading)
                if by_number:
                    scored = await _register_all(capture, by_number)
                    if scored:
                        result.method = "registration-by-number"

        # Then: the sets this user is already scanning. Cards arrive in batches from one set
        # far more often than not, and registration does not care that the hash failed.
        if not scored and _within_budget(started):
            prior = await _set_prior_candidates(session)
            if prior:
                scored = await _register_all(capture, prior)
                if scored:
                    result.method = "registration-set-prior"
        if not scored and fallback is not None and _within_budget(started):
            recovered = await identify(session, fallback, candidates=candidates)
            if recovered.card_tcgdex_id:
                recovered.method = "registration-from-original"
                return recovered
        if not scored:
            result.reason = "no candidate registered against the capture"
            return result

    scored.sort(key=lambda pair: pair[0].inliers, reverse=True)

    # TASK-020. Registration has ranked the candidates by how well the *picture* matches. Where
    # two candidates share artwork it cannot do better, so read the collector number and let it
    # re-rank. Corroboration only: see app/services/fusion.py for why it never overrides.
    # OCR only when it can change the answer.
    #
    # Measured: identification takes 4.49 s warm, of which reading the collector number is
    # 3.80 s — 85% of the work, for a signal that by definition cannot overturn a match the
    # artwork already settled beyond doubt. Tesseract is run up to 48 times per card (two
    # regions x two whitelists x four binarisations x three page-segmentation modes), which is
    # the price of reading 20/20 reliably and is worth paying only when the answer is in doubt.
    #
    # A card that registered with a large, clear margin is not in doubt: the runner-up is a
    # different picture, not a reprint of the same one. Reprints present as a *narrow* margin,
    # which is precisely the case this keeps OCR for. On a Pi, where every second is several,
    # this is the difference between keeping up and falling behind.
    top = scored[0][0].inliers
    second = scored[1][0].inliers if len(scored) > 1 else 0
    ambiguous = second > 0 and top < second * OCR_SKIP_MARGIN
    reading = (
        read_collector_number(capture)
        if ambiguous or top < OCR_SKIP_MIN_INLIERS
        else None
    )
    totals = await set_totals(session, [card.tcgdex_id for _, card in scored])
    scored, fusion = rescore(scored, reading, totals)

    # If the number corroborates nothing in the shortlist, the right card may simply never have
    # been offered — the hash prefilter misses reverse holos and heavy foils, which is exactly
    # when OCR is still perfectly readable. Go and fetch the cards that number names.
    if reading is not None and fusion.agreed is not True:
        extra = await candidates_by_number(session, reading)
        known = {card.tcgdex_id for _, card in scored}
        extra = [c for c in extra if c.tcgdex_id not in known]
        if extra:
            recovered = await _register_all(capture, extra)
            if recovered:
                scored = scored + recovered
                scored.sort(key=lambda pair: pair[0].inliers, reverse=True)
                totals = await set_totals(
                    session, [card.tcgdex_id for _, card in scored]
                )
                scored, fusion = rescore(scored, reading, totals)
                result.method = "registration+number"
    result.fusion = fusion.as_dict()

    best_match, best_card = scored[0]
    runner_up = scored[1][0].inliers if len(scored) > 1 else 0
    # Compare like with like: the runner-up's inliers must be weighted the same way the winner's
    # were, or a candidate the number ruled out still looks like a close second and drags the
    # margin down.
    if fusion.applied:
        best_weight = adjusted_weight(best_card, reading, totals)
        runner_weight = (
            adjusted_weight(scored[1][1], reading, totals) if len(scored) > 1 else 1.0
        )
        effective_best = best_match.inliers * best_weight
        effective_runner = runner_up * runner_weight
    else:
        effective_best = float(best_match.inliers)
        effective_runner = float(runner_up)

    result.card_tcgdex_id = best_card.tcgdex_id
    result.name = best_card.name
    result.set_name = best_card.set_name
    result.method = result.method if result.method != "none" else "registration"
    result.inliers = best_match.inliers
    result.rotation_deg = best_match.rotation_deg
    result.homography = best_match.homography
    result.hash_distance = best_card.distance
    result.candidates = [
        {**card.as_dict(), "inliers": match.inliers} for match, card in scored[:5]
    ]

    # Confidence blends how strong the winning match is with how far clear of the runner-up it
    # is. A hundred inliers means little if a second card also managed ninety.
    strength = min(1.0, best_match.inliers / 60.0)
    margin = (
        1.0
        if effective_runner == 0
        else min(1.0, (effective_best / max(effective_runner, 1.0)) / DECISIVE_MARGIN)
    )
    result.confidence = round(strength * (0.4 + 0.6 * margin), 4)

    decisive = effective_runner == 0 or effective_best >= effective_runner * DECISIVE_MARGIN
    result.needs_review = not (decisive and best_match.inliers >= MIN_INLIERS * 2)
    if result.needs_review:
        result.reason = (
            f"{best_match.inliers} inliers against {runner_up} for the runner-up — "
            "not decisive enough to accept without a look"
        )

    # A confident number that names a different card than the picture did is worth a human's
    # eye even when the registration looked decisive. This is the case where being wrong is
    # expensive: the two cards share art, so the crop looks perfect either way, and the only
    # thing separating a common from a chase card is the number nobody checked.
    if fusion.applied and fusion.agreed is False and not fusion.corroborated:
        # The number moved a different card to the top but does not actually support it. That is
        # a genuine conflict and wants a human.
        result.needs_review = True
        result.reason = fusion.note
    elif fusion.applied and fusion.agreed is False and fusion.corroborated:
        # The number moved a card up AND positively confirms it. That is the signal doing its
        # job, not a conflict: the picture could not separate two reprints and the printed
        # number did. Sending this to a human only to have them agree is the button-press this
        # is meant to remove, so accept it and record why.
        result.needs_review = False
        result.reason = None
    return result


async def _register_all(capture: np.ndarray, candidates: list) -> list:
    """Fetch and register against every candidate. Used only on the fallback path."""
    import asyncio

    usable = [c for c in candidates if c.image_url]
    breaker = _Breaker()
    async with httpx.AsyncClient(timeout=ART_TIMEOUT_S, follow_redirects=True) as client:
        arts = await asyncio.gather(
            *(_fetch_art(client, c.image_url, breaker) for c in usable)
        )

    scored = []
    for candidate, art in zip(usable, arts, strict=True):
        if art is None:
            continue
        match = register(capture, art, min_inliers=MIN_INLIERS, min_inlier_ratio=MIN_INLIER_RATIO)
        if match is not None:
            scored.append((match, candidate))
    return scored


async def _nearest_any_orientation(session: AsyncSession, capture: np.ndarray, limit: int):
    """Nearest cards, considering all four right-angle rotations of the capture."""
    seen: dict[str, object] = {}
    rotations = (
        capture,
        cv2.rotate(capture, cv2.ROTATE_90_CLOCKWISE),
        cv2.rotate(capture, cv2.ROTATE_180),
        cv2.rotate(capture, cv2.ROTATE_90_COUNTERCLOCKWISE),
    )
    for rotated in rotations:
        for candidate in await nearest(session, phash(rotated), limit=limit):
            existing = seen.get(candidate.tcgdex_id)
            if existing is None or candidate.distance < existing.distance:
                seen[candidate.tcgdex_id] = candidate
    return sorted(seen.values(), key=lambda c: c.distance)[: limit * 2]


async def _set_prior_candidates(session: AsyncSession, limit: int = SET_PRIOR_LIMIT):
    """Cards from the sets this user has already been identifying.

    A pure fallback: only consulted when the hash prefilter produced nothing that registered.
    """
    from app.models import InventoryItem

    recent_sets = (
        select(Card.set_id)
        .join(InventoryItem, InventoryItem.card_id == Card.id)
        .where(InventoryItem.card_id.is_not(None))
        .distinct()
    )
    rows = (
        await session.execute(
            select(Card.tcgdex_id, Card.name, CardSet.name, Card.image_url, Card.local_id)
            .join(CardSet, CardSet.id == Card.set_id)
            .where(Card.set_id.in_(recent_sets), Card.image_url.is_not(None))
            .limit(limit)
        )
    ).all()

    from app.services.art_index import Candidate

    return [
        Candidate(
            tcgdex_id=r[0],
            name=r[1],
            set_name=r[2],
            image_url=r[3],
            local_id=r[4],
            distance=99,
        )
        for r in rows
    ]


async def card_for(session: AsyncSession, tcgdex_id: str) -> Card | None:
    return (
        await session.execute(select(Card).where(Card.tcgdex_id == tcgdex_id))
    ).scalar_one_or_none()


async def set_name_for(session: AsyncSession, card: Card) -> str | None:
    row = (
        await session.execute(select(CardSet.name).where(CardSet.id == card.set_id))
    ).scalar_one_or_none()
    return row


async def recognise_item(session: AsyncSession, sku: str, user_id) -> dict:
    """Identify the card an inventory item holds, and record the result.

    Writes the identification rather than returning it, so the answer survives the request and
    shows up everywhere the item does. A result that is not decisive opens a review with the
    ranked candidates instead of writing a guess into inventory — the difference between a $5
    card and a $5,000 one is often one variant, so a coin flip here is worse than an unknown.
    """
    import cv2
    import numpy as np

    from app.enums import ImageKind, InventoryStatus, ReviewCategory, ReviewStatus
    from app.models import Image, InventoryItem, Review
    from app.storage import get_storage

    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.sku == sku, InventoryItem.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if item is None:
        return {"ok": False, "error": f"no such card: {sku}"}

    images = {
        image.kind: image
        for image in (
            await session.execute(select(Image).where(Image.inventory_item_id == item.id))
        )
        .scalars()
        .all()
    }
    processed = images.get(ImageKind.PROCESSED_FRONT)
    if processed is None:
        return {"ok": False, "sku": sku, "error": "no processed front to identify from"}

    storage = get_storage()

    def decode(image) -> np.ndarray | None:
        if image is None:
            return None
        buffer = np.frombuffer(storage.get(image.path), dtype=np.uint8)
        return cv2.imdecode(buffer, cv2.IMREAD_COLOR)

    capture = decode(processed)
    if capture is None:
        return {"ok": False, "sku": sku, "error": "processed front could not be decoded"}

    result = await identify(
        session, capture, fallback=decode(images.get(ImageKind.ORIGINAL_FRONT))
    )

    # A failed re-run must not degrade an identification that already succeeded.
    #
    # Recognition is re-run on every reprocess, and it is not deterministic across code changes:
    # a tightened time budget, a slower network or a colder cache can all make a marginal card
    # fail this time having succeeded last time. Overwriting unconditionally left CARD-000004
    # marked identified at confidence 0.0000 — a contradiction, and one that matters because
    # confidence routes reviews and is what "how much do we trust this" reads from.
    #
    # Only write the confidence when this run actually identified something, or when there is no
    # previous identification to protect.
    if result.card_tcgdex_id or item.card_id is None:
        item.identification_confidence = result.confidence
    if result.card_tcgdex_id and not result.needs_review:
        card = await card_for(session, result.card_tcgdex_id)
        if card is not None:
            item.card_id = card.id
            if item.status is InventoryStatus.CAPTURED:
                item.status = InventoryStatus.IDENTIFIED

    # One open identification review per item, refreshed rather than duplicated.
    existing = (
        await session.execute(
            select(Review).where(
                Review.inventory_item_id == item.id,
                Review.category == ReviewCategory.IDENTIFICATION,
                Review.status == ReviewStatus.OPEN,
            )
        )
    ).scalar_one_or_none()

    if result.needs_review:
        reason = result.reason or "identification was not decisive"
        if existing:
            existing.reason = reason
            existing.candidates = result.candidates
        else:
            session.add(
                Review(
                    user_id=item.user_id,
                    inventory_item_id=item.id,
                    category=ReviewCategory.IDENTIFICATION,
                    reason=reason,
                    candidates=result.candidates,
                )
            )
    elif existing:
        from datetime import datetime

        existing.status = ReviewStatus.RESOLVED
        existing.resolved_at = datetime.now().astimezone()
        existing.resolution = {"resolved_by": "recognition", "card": result.card_tcgdex_id}

    await session.flush()
    log.info(
        "recognition.done",
        sku=sku,
        card=result.card_tcgdex_id,
        inliers=result.inliers,
        confidence=result.confidence,
        needs_review=result.needs_review,
    )
    return {"ok": True, "sku": sku, **result.as_dict()}


async def resolve_variant(session: AsyncSession, sku: str, user_id) -> dict:
    """Assign the card's variant where it is unambiguous, and ask where it is not.

    A card printed in only one variant needs no measurement — TCGdex lists exactly one, so the
    card in hand is that one. That covers a real share of a collection, full-art ex cards among
    them, and assigning it is free.

    Where a card was printed Normal *and* Reverse Holo, the two are physically identical except
    for foil on the card body, and **foil cannot be read reliably from a single hand-held
    capture**. Measured across twelve real captures: the same Gourgeist ex photographed twice
    returned foil coverage of 0.094 and 0.164, because foil only reveals itself at particular
    angles to the light. A classifier on that signal would be guessing with a confident face,
    and the guess is often the difference between a common and a card worth twenty times more.

    So it opens a review listing the candidate variants. That is the correct answer until the
    capture rig arrives with controlled lighting, which is what makes the measurement possible.
    """
    from app.enums import ReviewCategory, ReviewStatus
    from app.models import CardVariant, InventoryItem, Review

    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.sku == sku, InventoryItem.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if item is None:
        return {"ok": False, "error": f"no such card: {sku}"}
    if item.card_id is None:
        return {"ok": False, "sku": sku, "error": "not identified yet"}

    variants = (
        (
            await session.execute(
                select(CardVariant)
                .where(CardVariant.card_id == item.card_id)
                .order_by(CardVariant.label)
            )
        )
        .scalars()
        .all()
    )
    if not variants:
        return {"ok": False, "sku": sku, "error": "card has no variants recorded"}

    candidates = [
        {"variant_id": str(v.id), "label": v.label, "type": v.type} for v in variants
    ]

    # No variant is ever assigned without a person confirming it, not even when the catalogue
    # lists exactly one.
    #
    # Auto-assigning the sole variant looked free and is not. It is right only if the catalogue
    # is complete and the card in hand is the printing the catalogue knows about — and neither
    # holds reliably. TCGdex misses printings, and promos, regional runs and error prints all
    # show up as "one variant" for a card that physically has more. The failure is silent: the
    # listing goes out naming a variant nobody checked, priced as that variant.
    #
    # A single-variant card is still cheap for the operator, because the review offers one
    # button. Confirming is a click; being wrong about foil is the difference between a common
    # and a card worth twenty times more.

    existing = (
        await session.execute(
            select(Review).where(
                Review.inventory_item_id == item.id,
                Review.category == ReviewCategory.VARIANT,
                Review.status == ReviewStatus.OPEN,
            )
        )
    ).scalar_one_or_none()
    if len(variants) == 1:
        reason = (
            f"Catalogue lists one printing ({variants[0].label}). Confirm it — a variant is "
            "never assumed, because the catalogue is not always complete."
        )
    else:
        reason = (
            f"{len(variants)} variants printed ({', '.join(v.label for v in variants)}). "
            "Foil cannot be read reliably from a single hand-held capture, and the difference "
            "is priced very differently — pick one."
        )
    if existing:
        existing.reason = reason
        existing.candidates = candidates
    else:
        session.add(
            Review(
                user_id=item.user_id,
                inventory_item_id=item.id,
                category=ReviewCategory.VARIANT,
                reason=reason,
                candidates=candidates,
            )
        )
    await session.flush()
    return {
        "ok": True,
        "sku": sku,
        "resolved": False,
        "candidates": candidates,
        "reason": reason,
    }


async def recognise_and_finish(session: AsyncSession, sku: str, user_id) -> dict:
    """Identify a card and do everything that follows from knowing what it is.

    Identification on its own leaves a card half-finished: the crop is still the one edge
    detection guessed at, there is no listing image, and no variant is assigned. The HTTP
    endpoint has always run the whole sequence; the queue did not, so cards recognised
    automatically came out without listing images or a variant while cards recognised by a
    button press came out complete.

    One sequence, called from both, so "recognised" means the same thing either way.
    """
    from app.models import InventoryItem
    from app.services.listing_images import build_for_item
    from app.services.recrop import recrop_front

    result = await recognise_item(session, sku, user_id)
    if not result.get("ok"):
        return result
    await session.commit()

    # Recognition is the second half of rectification: the card's own art is a far better
    # boundary reference than any edge in the photograph.
    if result.get("card") and not result.get("needs_review"):
        result["recrop"] = await recrop_front(session, sku, user_id)
        await session.commit()

    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.sku == sku, InventoryItem.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if item is not None:
        result["listing_images"] = await build_for_item(session, item)
        await session.commit()

    if result.get("card") and not result.get("needs_review"):
        result["variant"] = await resolve_variant(session, sku, user_id)
        await session.commit()
    return result
