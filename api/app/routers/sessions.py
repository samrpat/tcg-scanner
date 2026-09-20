"""Scanning sessions — the batch you are working on right now.

Two thousand cards is not one sitting. Each evening's scanning gets approved, priced and uploaded
as a unit, and "what am I working on" needs an answer narrower than "everything I have ever
scanned". A session is that unit.

The open session is simply **the newest one with no `closed_at`**, rather than a flag that can
disagree with itself. Starting a new session closes the current one; reopening an old session
makes it the target again, which is how a batch gets added to after the fact.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_session
from app.enums import ImageKind
from app.logging_setup import get_logger
from app.models import BatchSection, Image, InventoryItem, ScanSession, User
from app.routers.capture import current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])
log = get_logger(__name__)


class SessionIn(BaseModel):
    name: str | None = None
    note: str | None = None
    # Crop and render, never identify. Defaults to whatever `scanner_mode` says, so the app's
    # overall mode is set once rather than remembered per batch.
    photos_only: bool | None = None
    # Whether the new batch becomes the one scans go into. Creating a batch purely to move
    # cards into it must not redirect the scanner mid-pile, so that case passes False and the
    # batch is born closed.
    open: bool = True


async def open_session(session: AsyncSession, user: User) -> ScanSession | None:
    """The session new captures join, or None when none is open."""
    return (
        await session.execute(
            select(ScanSession)
            .where(ScanSession.user_id == user.id, ScanSession.closed_at.is_(None))
            .order_by(ScanSession.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def ensure_open_session(session: AsyncSession, user: User) -> ScanSession:
    """The open session, creating one if there is none.

    Called from capture, so a card is never scanned into nowhere. Someone who never touches
    sessions gets one anyway and never has to think about it.
    """
    existing = await open_session(session, user)
    if existing is not None:
        return existing
    created = ScanSession(
        user_id=user.id,
        name=await _default_name(session, user),
        started_at=datetime.now(UTC),
        photos_only=settings.scanner_mode,
    )
    session.add(created)
    await session.flush()
    log.info("session.auto_created", session_id=str(created.id), name=created.name)
    return created


async def _default_name(session: AsyncSession, user: User) -> str:
    """Today's date and this batch's number within the day.

    A timestamp sorts correctly and reads badly — "Session 05 Sep 23:41" tells you nothing you
    wanted to know. "2026-09-05 · Batch 2" says which pile it is, which is the only thing
    anybody asks about a batch.
    """
    return await _generated_name(session, user, datetime.now(UTC))


async def _generated_name(session: AsyncSession, user: User, when: datetime) -> str:
    """The generated name for a batch started at `when`.

    Taken from the batch's own start rather than from the clock, so clearing the name of a
    batch from last week restores *its* date and not today's.

    Counts the batches that started **before** it that day, which gives the right ordinal
    whether the batch already exists (a rename) or is about to (a new one).
    """
    day = when.date()
    earlier = (
        await session.execute(
            select(func.count(ScanSession.id)).where(
                ScanSession.user_id == user.id,
                func.date(ScanSession.started_at) == day,
                ScanSession.started_at < when,
            )
        )
    ).scalar_one()
    return f"{day.isoformat()} · Batch {earlier + 1}"


@router.get("")
async def list_sessions(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Every session, newest first, with how many cards each holds."""
    rows = (
        (
            await session.execute(
                select(ScanSession)
                .where(ScanSession.user_id == user.id)
                .order_by(ScanSession.started_at.desc())
            )
        )
        .scalars()
        .all()
    )
    counts = dict(
        (
            await session.execute(
                select(InventoryItem.session_id, func.count(InventoryItem.id))
                .where(InventoryItem.user_id == user.id)
                .group_by(InventoryItem.session_id)
            )
        ).all()
    )
    current = await open_session(session, user)

    return {
        "sessions": [
            {
                "id": str(row.id),
                "name": row.name,
                "note": row.note,
                "started_at": row.started_at.isoformat(),
                "closed_at": row.closed_at.isoformat() if row.closed_at else None,
                "open": row.closed_at is None,
                "photos_only": row.photos_only,
                "corner_shots": row.corner_shots,
                "current": current is not None and row.id == current.id,
                "cards": counts.get(row.id, 0),
            }
            for row in rows
        ],
        "current_id": str(current.id) if current else None,
        # Cards scanned before sessions existed, or whose session was deleted.
        "unassigned": counts.get(None, 0),
    }


@router.post("")
async def start_session(
    body: SessionIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Start a new session, closing whichever one is open.

    Closing is not deleting: the previous batch keeps its cards and can be reopened.
    """
    now = datetime.now(UTC)
    current = await open_session(session, user)
    if current is not None and body.open:
        current.closed_at = now

    created = ScanSession(
        user_id=user.id,
        name=(body.name or "").strip() or await _default_name(session, user),
        note=(body.note or "").strip() or None,
        started_at=now,
        # A batch created only as somewhere to put cards is closed from the start: "open" here
        # means "scans land here", and exactly one batch may claim that at a time.
        closed_at=None if body.open else now,
        photos_only=(
            settings.scanner_mode if body.photos_only is None else body.photos_only
        ),
    )
    session.add(created)
    await session.commit()
    await session.refresh(created)
    log.info(
        "session.started",
        session_id=str(created.id),
        name=created.name,
        photos_only=created.photos_only,
    )
    return {
        "id": str(created.id),
        "name": created.name,
        "photos_only": created.photos_only,
        "open": created.closed_at is None,
        "closed": str(current.id) if current and body.open else None,
    }


class RenameIn(BaseModel):
    name: str | None = None
    note: str | None = None


@router.patch("/{session_id}")
async def rename_session(
    session_id: uuid.UUID,
    body: RenameIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Rename a batch, or change its note.

    The generated name — the date and the batch number within it — sorts correctly and says
    which pile it is, which is all it needs to do while the pile is on the bench. Afterwards it
    often is not: "Binder A, holos" or "eBay lot 3" is what the operator actually calls it, and
    a name nobody recognises makes the archive list something to search rather than read.

    Only the label changes. Nothing keys off a batch's name — the id is the identity — so this
    can never lose a card.

    An empty name restores the generated one rather than leaving a nameless batch in the list.
    """
    target = (
        await session.execute(
            select(ScanSession).where(
                ScanSession.id == session_id, ScanSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="no such session")

    if body.name is not None:
        cleaned = body.name.strip()[:120]
        target.name = cleaned or await _generated_name(session, user, target.started_at)
    if body.note is not None:
        target.note = body.note.strip()[:2000] or None

    await session.commit()
    log.info("session.renamed", session_id=str(target.id), name=target.name)
    return {"id": str(target.id), "name": target.name, "note": target.note}


@router.post("/{session_id}/reopen")
async def reopen_session(
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Make an earlier session the one new scans join again."""
    target = (
        await session.execute(
            select(ScanSession).where(
                ScanSession.id == session_id, ScanSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="no such session")

    now = datetime.now(UTC)
    current = await open_session(session, user)
    if current is not None and current.id != target.id:
        current.closed_at = now
    target.closed_at = None
    # Reopening makes it the newest open session, which is what "current" means.
    target.started_at = now
    await session.commit()
    return {"id": str(target.id), "name": target.name, "current": True}


@router.delete("/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    cards: str = Query("keep", pattern="^(keep|delete)$"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Delete a session, and optionally the cards scanned in it.

    `cards=keep` is the default and only removes the grouping — the cards stay, unassigned.
    Removing a grouping is not the same as removing photographs of real cards, and defaulting
    the other way is how someone loses an evening's work by tidying up.

    `cards=delete` is the deliberate one and does remove them, images and all.
    """
    from app.services.purge import purge_images, purge_rows

    target = (
        await session.execute(
            select(ScanSession).where(
                ScanSession.id == session_id, ScanSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="no such session")

    removed: list[str] = []
    if cards == "delete":
        skus = (
            (
                await session.execute(
                    select(InventoryItem.sku).where(
                        InventoryItem.user_id == user.id,
                        InventoryItem.session_id == target.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        removed = await purge_rows(session, user, list(skus))

    await session.delete(target)
    await session.commit()
    purge_images(removed)
    log.info("session.deleted", session_id=str(session_id), cards_removed=len(removed))
    return {"deleted": str(session_id), "cards_removed": len(removed)}


class MoveIn(BaseModel):
    skus: list[str]


@router.post("/{session_id}/cards")
async def move_cards(
    session_id: uuid.UUID,
    body: MoveIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Move cards into this batch.

    Batches get wrong. A pile is scanned across a break and lands in two, a card belonging to
    yesterday's lot is shot today, or a batch is archived one card early. Every one of those is
    a grouping mistake, and the fix should be a grouping change — not a rescan, and not a
    hand-written UPDATE against the database.

    Only `session_id` moves; nothing is reprocessed and no photograph is touched, because which
    pile a card was counted in has no bearing on the pictures of it.

    Unknown SKUs are reported rather than raising, so moving twenty cards does not fail whole
    because one of them was deleted in between. Cards already here are counted as moved, which
    makes the call idempotent — a retry after a dropped response does not error.
    """
    wanted = [s.strip().upper() for s in body.skus if s.strip()]
    if not wanted:
        raise HTTPException(status_code=400, detail="no cards given")

    target = (
        await session.execute(
            select(ScanSession).where(
                ScanSession.id == session_id, ScanSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="no such session")

    items = (
        (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.user_id == user.id, InventoryItem.sku.in_(wanted)
                )
            )
        )
        .scalars()
        .all()
    )
    found = {item.sku for item in items}
    # The batches losing cards, so the caller knows which counts went stale.
    sources = {str(item.session_id) for item in items if item.session_id != target.id}

    for item in items:
        item.session_id = target.id
    await session.commit()

    log.info(
        "session.cards_moved",
        session_id=str(target.id),
        moved=len(items),
        missing=len(wanted) - len(found),
    )
    return {
        "session_id": str(target.id),
        "name": target.name,
        "moved": len(items),
        "missing": sorted(set(wanted) - found),
        "sources": sorted(sources - {"None"}),
    }


class CornersIn(BaseModel):
    on: bool
    # How much of the card each corner shot covers. Smaller closes in on the corner itself.
    fraction: float | None = None


@router.post("/{session_id}/corners")
async def set_corner_shots(
    session_id: uuid.UUID,
    body: CornersIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Turn corner close-ups on or off for this batch, and make the ones that are missing.

    Turning them on is not just a flag. The switch used to live in the browser and only decided
    what went into a download, so a batch scanned with it on still contained no corners — nothing
    ever cut them, and the per-card button is not something anyone presses a hundred times. So
    this builds every card in the batch that has none, and from here on the worker cuts them as
    each card finishes.

    Turning them off **does not delete anything.** It stops new ones being cut and leaves them
    out of the download. Deleting the files is a separate, explicit request, because a switch
    that quietly destroys work on its way past is a switch nobody can use with confidence.
    """
    from app.services import corner_details

    target = (
        await session.execute(
            select(ScanSession).where(
                ScanSession.id == session_id, ScanSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="no such session")

    target.corner_shots = body.on
    built: list[str] = []
    already: list[str] = []

    if body.on:
        items = (
            (
                await session.execute(
                    select(InventoryItem)
                    .options(selectinload(InventoryItem.images))
                    .where(
                        InventoryItem.user_id == user.id,
                        InventoryItem.session_id == target.id,
                    )
                    .order_by(InventoryItem.sku)
                )
            )
            .scalars()
            .all()
        )
        fraction = body.fraction or corner_details.CORNER_FRACTION
        for item in items:
            # Already cut ones are left alone rather than re-cut: this is the switch being
            # turned on, not a request to redo work, and re-encoding a hundred cards to reach
            # the same bytes is a minute of a Pi's time for nothing.
            if any(image.kind in corner_details.QUADRANTS for image in item.images):
                already.append(item.sku)
                continue
            result = await corner_details.build_for_item(session, item, fraction)
            (built if result["rendered"] else already).append(item.sku)

    await session.commit()
    log.info(
        "session.corner_shots",
        session_id=str(target.id),
        on=body.on,
        built=len(built),
    )
    return {
        "session_id": str(target.id),
        "corner_shots": target.corner_shots,
        "built": built,
        "already_had_them": already,
    }


@router.delete("/{session_id}/corners")
async def delete_corner_shots(
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Delete this batch's corner close-ups from disk.

    They are derived from the listing front, so this costs only the seconds to cut them again —
    which is why it is offered at all. On two thousand cards it is the difference between eight
    thousand files and none.
    """
    from app.services import corner_details
    from app.storage import get_storage

    target = (
        await session.execute(
            select(ScanSession).where(
                ScanSession.id == session_id, ScanSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="no such session")

    rows = (
        (
            await session.execute(
                select(Image)
                .where(
                    Image.kind.in_(list(corner_details.QUADRANTS)),
                    Image.inventory_item_id.in_(
                        select(InventoryItem.id).where(
                            InventoryItem.user_id == user.id,
                            InventoryItem.session_id == target.id,
                        )
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    storage = get_storage()
    removed = 0
    for row in rows:
        try:
            storage.delete(row.path)
        except Exception as exc:  # noqa: BLE001 - a stuck file must not strand the rest
            log.warning("session.corner_delete_failed", path=row.path, error=str(exc))
        await session.delete(row)
        removed += 1

    await session.commit()
    log.info("session.corners_deleted", session_id=str(target.id), removed=removed)
    return {"session_id": str(target.id), "removed": removed}


def export_order(corners: bool, kind: str) -> list[tuple[ImageKind, str]]:
    """Which photographs go in a download, in the order they are numbered.

    The gallery image lands first in every uploader that sorts by filename, so the front leads.
    The extra shots sit third, ahead of the corner close-ups: on a holo that photograph is the
    one that sells the card, and a buyer scrolling a gallery should reach it before four
    pictures of the edges.

    Shared with the plan endpoint on purpose. A preview of what you are about to download that
    is computed separately from the download is a preview that will eventually disagree with it.
    """
    order = [
        (ImageKind.LISTING_FRONT, "front"),
        (ImageKind.LISTING_BACK, "back"),
        (ImageKind.EXTRA_1, "extra-1"),
        (ImageKind.EXTRA_2, "extra-2"),
        (ImageKind.EXTRA_3, "extra-3"),
    ]
    if corners:
        order += [
            (ImageKind.DETAIL_FRONT_TL, "corner-top-left"),
            (ImageKind.DETAIL_FRONT_TR, "corner-top-right"),
            (ImageKind.DETAIL_FRONT_BL, "corner-bottom-left"),
            (ImageKind.DETAIL_FRONT_BR, "corner-bottom-right"),
        ]
    if kind == "all":
        order += [
            (ImageKind.ORIGINAL_FRONT, "original-front"),
            (ImageKind.ORIGINAL_BACK, "original-back"),
        ]
    return order


def photo_plan(items, order: list[tuple[ImageKind, str]]) -> dict:
    """How many photographs each card would contribute, grouped by that count.

    This exists because of how bulk uploaders work. CardUploader and its kin are handed a flat
    folder and told *how many photographs each card has*; they then chunk the sorted list into
    groups of that size. The count has to be the same for every card in the upload — one card
    with a seventh photograph shifts every card after it by one, and the result is a listing
    illustrated with someone else's card.

    Extra shots break that by design: they are the extra photograph, on the few cards that
    earn one. So a batch containing both has to be uploaded as two, and the split is by count.
    """
    groups: dict[int, list[str]] = {}
    empty: list[str] = []
    for item in items:
        held = {image.kind for image in item.images}
        count = sum(1 for image_kind, _ in order if image_kind in held)
        if count == 0:
            empty.append(item.sku)
        else:
            groups.setdefault(count, []).append(item.sku)
    return {
        "groups": [
            {"photos": count, "cards": len(skus), "skus": sorted(skus)}
            for count, skus in sorted(groups.items())
        ],
        # Reported rather than silently dropped. A card that contributes nothing is missing from
        # the upload, and finding that out from a listing with no picture is the expensive way.
        "without_photos": sorted(empty),
    }


class _Sink:
    """A write-only file for `zipfile`, which hands back whatever has been written.

    `zipfile` needs somewhere to write and a `tell()` to track offsets. It does not need to
    seek, as long as it knows it cannot — with `seekable()` false it writes data descriptors
    after each entry instead of going back to patch the headers.

    So this pretends to be a file, keeps only what has not been sent yet, and `drain()` empties
    it. Peak memory is one photograph, not one archive.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._written = 0

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        self._written += len(data)
        return len(data)

    def tell(self) -> int:
        return self._written

    def flush(self) -> None:
        return None

    def seekable(self) -> bool:
        return False

    def drain(self) -> Iterator[bytes]:
        chunks, self._chunks = self._chunks, []
        yield from chunks


def _safe_name(name: str) -> str:
    """A section name as a folder name. Sections are named by hand and a name with a slash in
    it would otherwise invent a directory level inside the zip."""
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", name).strip(" -.")
    return cleaned[:60] or "section"


def group_folder(count: int) -> str:
    """The folder a card with this many photographs goes in.

    Named as the number to type into the uploader, because that is the only thing anyone needs
    from it.
    """
    return f"{count}-photos-per-card"


class SectionIn(BaseModel):
    name: str


class SectionMoveIn(BaseModel):
    skus: list[str]


async def current_section(session: AsyncSession, batch: ScanSession) -> BatchSection | None:
    """The section new scans join.

    Whichever one the batch points at, and failing that the last in order — which is the right
    answer for a batch that has never been pointed anywhere, and for the moment just after
    creating the first section.

    The pointer exists because "the last one" is wrong as soon as you want to go back. Finding
    three more reverse holos at the bottom of the box should not mean reordering the sections.
    """
    if batch.active_section_id is not None:
        active = await session.get(BatchSection, batch.active_section_id)
        if active is not None and active.session_id == batch.id:
            return active

    return (
        await session.execute(
            select(BatchSection)
            .where(BatchSection.session_id == batch.id)
            .order_by(BatchSection.position.desc(), BatchSection.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _batch_or_404(session: AsyncSession, session_id, user: User) -> ScanSession:
    target = (
        await session.execute(
            select(ScanSession).where(
                ScanSession.id == session_id, ScanSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="no such session")
    return target


@router.get("/{session_id}/sections")
async def list_sections(
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """The divisions in this batch, in order, with how many cards each holds."""
    target = await _batch_or_404(session, session_id, user)
    rows = (
        (
            await session.execute(
                select(BatchSection)
                .where(BatchSection.session_id == target.id)
                .order_by(BatchSection.position, BatchSection.created_at)
            )
        )
        .scalars()
        .all()
    )
    counts = dict(
        (
            await session.execute(
                select(InventoryItem.section_id, func.count(InventoryItem.id))
                .where(InventoryItem.session_id == target.id)
                .group_by(InventoryItem.section_id)
            )
        ).all()
    )
    current = await current_section(session, target)
    return {
        "sections": [
            {
                "id": str(row.id),
                "name": row.name,
                "position": row.position,
                "cards": counts.get(row.id, 0),
                "current": current is not None and row.id == current.id,
            }
            for row in rows
        ],
        # Cards scanned before any section existed, or whose section was deleted.
        "unsectioned": counts.get(None, 0),
    }


@router.post("/{session_id}/sections")
async def create_section(
    session_id: uuid.UUID,
    body: SectionIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Start a new division, and send new scans into it.

    The physical act is putting down one pile and picking up the next, so this is one button
    and one name — not a dialog with a position field. It goes on the end, which is where the
    pile you are about to scan belongs.
    """
    target = await _batch_or_404(session, session_id, user)
    name = body.name.strip()[:120]
    if not name:
        raise HTTPException(status_code=422, detail="a section needs a name")

    highest = (
        await session.execute(
            select(func.max(BatchSection.position)).where(
                BatchSection.session_id == target.id
            )
        )
    ).scalar_one()

    created = BatchSection(
        session_id=target.id, name=name, position=(highest or 0) + 1
    )
    session.add(created)
    await session.flush()
    # You make a section because you are about to scan into it. Anything else would need a
    # second action to express the obvious one.
    target.active_section_id = created.id
    await session.commit()
    await session.refresh(created)
    log.info("section.created", session_id=str(target.id), name=created.name)
    return {"id": str(created.id), "name": created.name, "position": created.position}


@router.post("/{session_id}/sections/{section_id}/activate")
async def activate_section(
    session_id: uuid.UUID,
    section_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Send new scans into this section. `none` sends them into no section at all.

    Called from the scan screen, mid-pile, with a phone in one hand — so it is one request
    that takes effect on the very next shutter press, with nothing to confirm.
    """
    target = await _batch_or_404(session, session_id, user)

    if section_id == "none":
        target.active_section_id = None
        await session.commit()
        return {"active": None}

    try:
        wanted = uuid.UUID(section_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="not a section id") from exc

    row = (
        await session.execute(
            select(BatchSection).where(
                BatchSection.id == wanted, BatchSection.session_id == target.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="no such section")

    target.active_section_id = row.id
    await session.commit()
    log.info("section.activated", session_id=str(target.id), name=row.name)
    return {"active": {"id": str(row.id), "name": row.name}}


@router.patch("/{session_id}/sections/{section_id}")
async def rename_section(
    session_id: uuid.UUID,
    section_id: uuid.UUID,
    body: SectionIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    target = await _batch_or_404(session, session_id, user)
    row = (
        await session.execute(
            select(BatchSection).where(
                BatchSection.id == section_id, BatchSection.session_id == target.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="no such section")
    name = body.name.strip()[:120]
    if not name:
        raise HTTPException(status_code=422, detail="a section needs a name")
    row.name = name
    await session.commit()
    return {"id": str(row.id), "name": row.name}


@router.delete("/{session_id}/sections/{section_id}")
async def delete_section(
    session_id: uuid.UUID,
    section_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Remove a division. The cards stay in the batch, without one.

    Never the cards. Deleting a heading is a statement about the grouping, exactly as deleting
    a batch is — and losing an evening's scanning by tidying up a label would be unforgivable.
    """
    target = await _batch_or_404(session, session_id, user)
    row = (
        await session.execute(
            select(BatchSection).where(
                BatchSection.id == section_id, BatchSection.session_id == target.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="no such section")

    freed = (
        await session.execute(
            select(func.count(InventoryItem.id)).where(InventoryItem.section_id == row.id)
        )
    ).scalar_one()
    await session.delete(row)
    await session.commit()
    log.info("section.deleted", section_id=str(section_id), cards_kept=freed)
    return {"deleted": str(section_id), "cards_kept": freed}


@router.post("/{session_id}/sections/{section_id}/cards")
async def move_into_section(
    session_id: uuid.UUID,
    section_id: str,
    body: SectionMoveIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Move cards into a section, or out of every section with `section_id=none`.

    Scanning into the wrong pile happens; so does deciding a card belongs in the other one
    after looking at it large. Only the grouping changes.
    """
    target = await _batch_or_404(session, session_id, user)

    destination: uuid.UUID | None = None
    if section_id != "none":
        try:
            wanted_id = uuid.UUID(section_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="not a section id") from exc
        row = (
            await session.execute(
                select(BatchSection).where(
                    BatchSection.id == wanted_id, BatchSection.session_id == target.id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="no such section")
        destination = row.id

    wanted = [s.strip().upper() for s in body.skus if s.strip()]
    if not wanted:
        raise HTTPException(status_code=400, detail="no cards given")

    items = (
        (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.user_id == user.id,
                    InventoryItem.session_id == target.id,
                    InventoryItem.sku.in_(wanted),
                )
            )
        )
        .scalars()
        .all()
    )
    for item in items:
        item.section_id = destination
    await session.commit()

    found = {item.sku for item in items}
    return {
        "section_id": section_id,
        "moved": len(items),
        # A card named here but not in this batch is reported rather than silently ignored.
        "missing": sorted(set(wanted) - found),
    }


@router.get("/{session_id}/photos.zip")
async def download_photos(
    session_id: uuid.UUID,
    kind: str = Query("listing", pattern="^(listing|all)$"),
    # Flat by default: one folder of files is what an uploader wants to be pointed at, and the
    # SKU is already in every filename, so the folder-per-card only adds clicks.
    layout: str = Query("flat", pattern="^(flat|folders|count)$"),
    # Corner close-ups quadruple the file count and the upload time. Worth it on a card
    # somebody will zoom into, and dead weight on a bulk common. Unset follows the batch's own
    # switch, so the download matches what the batch was told to make; passing it explicitly
    # overrides that for one download without changing the batch.
    corners: bool | None = Query(None),
    # A folder per section, composed with `layout` rather than replacing it: the operator
    # sorted the pile into reverse holos and normals *and* needs a fixed photo count per
    # upload, and those are two different questions about the same download.
    by_section: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    """Every photograph from this batch, as a zip, ready to drop into another tool.

    This is the whole point of a photos-only batch. The rendering — rectified to a true
    88 x 63 mm, squared, with a known margin of real background and each corner cut out — is the
    part that other listing tools do not do, and a folder of those images is what they want.

    Files are named `CARD-000001-1-front.jpg`, `-2-back.jpg`, `-3-corner-tl.jpg` and so on. The
    number is there because uploaders order photographs by filename and the first one becomes
    the gallery image; leaving the order to chance puts a corner crop on the search results page.

    `kind=listing` gives the presentation renders, which is what a listing wants.
    `kind=all` adds the originals, for anything that would rather do its own cropping.
    """
    import zipfile

    from app.storage import get_storage

    target = (
        await session.execute(
            select(ScanSession).where(
                ScanSession.id == session_id, ScanSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="no such session")

    if corners is None:
        corners = target.corner_shots
    ORDER = export_order(corners, kind)

    items = (
        (
            await session.execute(
                select(InventoryItem)
                .options(selectinload(InventoryItem.images))
                .where(
                    InventoryItem.user_id == user.id,
                    InventoryItem.session_id == target.id,
                )
                .order_by(InventoryItem.sku)
            )
        )
        .scalars()
        .all()
    )

    plan = photo_plan(items, ORDER)
    counts = {
        sku: group["photos"] for group in plan["groups"] for sku in group["skus"]
    }

    folders: dict[uuid.UUID | None, str] = {}
    if by_section:
        sections = (
            (
                await session.execute(
                    select(BatchSection)
                    .where(BatchSection.session_id == target.id)
                    .order_by(BatchSection.position, BatchSection.created_at)
                )
            )
            .scalars()
            .all()
        )
        # Numbered, so the folders sort in the order the piles were scanned rather than
        # alphabetically — "01 Reverse holo NM" before "02 Reverse holo LP".
        for index, row in enumerate(sections, start=1):
            folders[row.id] = f"{index:02d} {_safe_name(row.name)}"
        folders[None] = "00 unsorted"

    storage = get_storage()

    # Streamed, not assembled.
    #
    # This used to build the whole archive in a BytesIO and return it in one piece. At 110
    # cards that is a two-gigabyte object inside a container limited to one, so the process
    # was OOM-killed and nginx answered 502 — the download did not fail, the API died. And it
    # would have died on a Pi far sooner, because the ceiling was never the real limit: the
    # archive grows with the collection and memory does not.
    #
    # Writing to a temporary file instead would bound memory but doubles the disk traffic and
    # makes the browser wait for the whole thing before the first byte. `zipfile` can write to
    # an unseekable stream, so the zip is generated as it is sent: one file in memory at a
    # time, whatever the batch.
    #
    # The cost is no Content-Length, so the browser shows an unknown-size download. That is a
    # progress bar against never finishing at all.
    ordered = []
    for item in items:
        by_kind = {image.kind: image for image in item.images}
        # Numbered per card, in sequence, rather than by a fixed position in ORDER. Most
        # cards carry no extra shots, and a fixed scheme would leave every one of them with
        # a gap between -2-back and -6-corner — which reads as a missing photograph.
        position = 0
        for image_kind, label in ORDER:
            stored = by_kind.get(image_kind)
            if stored is None:
                continue
            position += 1
            name = f"{item.sku}-{position}-{label}.jpg"
            if layout == "folders":
                path = f"{item.sku}/{name}"
            elif layout == "count":
                path = f"{group_folder(counts[item.sku])}/{name}"
            else:
                path = name
            if by_section:
                path = f"{folders.get(item.section_id, '00 unsorted')}/{path}"
            ordered.append((path, stored.path, item.sku))

    if not ordered:
        raise HTTPException(
            status_code=409,
            detail=(
                "No photographs in this batch yet — scan some cards, or wait for processing "
                "to finish."
            ),
        )

    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", target.name).strip("-") or "batch"
    log.info(
        "photos_zip.streaming",
        session=str(session_id),
        files=len(ordered),
        cards=len(items),
    )

    def build() -> Iterator[bytes]:
        sink = _Sink()
        # ZIP_STORED, not DEFLATE: JPEGs are already compressed, so deflating them spends CPU
        # on a Pi to save almost nothing.
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as archive:
            for path, source, sku in ordered:
                try:
                    payload = storage.get(source)
                except Exception as exc:  # noqa: BLE001 - one missing file, not a failed zip
                    log.warning("photos_zip.skipped", sku=sku, error=str(exc))
                    continue
                archive.writestr(path, payload)
                yield from sink.drain()
        yield from sink.drain()

    return StreamingResponse(
        build(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}-photos.zip"',
            "X-Photo-Count": str(len(ordered)),
            "X-Card-Count": str(len(items)),
            # e.g. "6x10; 7x3" — ten cards with six photographs, three with seven. The numbers
            # to type into the uploader, and how many listings each will produce.
            "X-Photo-Groups": "; ".join(
                f"{group['photos']}x{group['cards']}" for group in plan["groups"]
            )[:400],
            "Access-Control-Expose-Headers": (
                "X-Photo-Count, X-Card-Count, X-Photo-Groups"
            ),
        },
    )


@router.post("/{session_id}/archive")
async def archive_session(
    session_id: uuid.UUID,
    name: str | None = Query(None, max_length=120),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Close this batch and open a fresh one, ready for the next pile.

    The one action between finishing a scanning run and starting the next. Archiving keeps
    everything: the cards, their photographs, the ability to re-download the folder. It only
    stops new scans joining it, which is the whole of what "done with that pile" means.
    """
    target = (
        await session.execute(
            select(ScanSession).where(
                ScanSession.id == session_id, ScanSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="no such session")

    now = datetime.now(UTC)
    target.closed_at = now
    created = ScanSession(
        user_id=user.id,
        name=(name or "").strip() or await _default_name(session, user),
        started_at=now,
        photos_only=settings.scanner_mode,
        # Inherited, not reset. "Archive and start next" is one pile following another, and
        # having the corner switch silently flip back on between them is how a batch ends up
        # with four unwanted files per card.
        corner_shots=target.corner_shots,
    )
    session.add(created)
    await session.commit()
    await session.refresh(created)

    count = (
        await session.execute(
            select(func.count(InventoryItem.id)).where(
                InventoryItem.session_id == target.id
            )
        )
    ).scalar_one()

    log.info("session.archived", archived=str(target.id), cards=count, next=str(created.id))
    return {
        "archived": {"id": str(target.id), "name": target.name, "cards": count},
        "now_scanning_into": {"id": str(created.id), "name": created.name},
    }
