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
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_session
from app.logging_setup import get_logger
from app.models import InventoryItem, ScanSession, User
from app.routers.capture import current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])
log = get_logger(__name__)


class SessionIn(BaseModel):
    name: str | None = None
    note: str | None = None
    # Crop and render, never identify. Defaults to whatever `scanner_mode` says, so the app's
    # overall mode is set once rather than remembered per batch.
    photos_only: bool | None = None


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
    today = datetime.now(UTC).date()
    made_today = (
        await session.execute(
            select(func.count(ScanSession.id)).where(
                ScanSession.user_id == user.id,
                func.date(ScanSession.started_at) == today,
            )
        )
    ).scalar_one()
    return f"{today.isoformat()} · Batch {made_today + 1}"


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
    if current is not None:
        current.closed_at = now

    created = ScanSession(
        user_id=user.id,
        name=(body.name or "").strip() or await _default_name(session, user),
        note=(body.note or "").strip() or None,
        started_at=now,
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
        "closed": str(current.id) if current else None,
    }


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


@router.get("/{session_id}/photos.zip")
async def download_photos(
    session_id: uuid.UUID,
    kind: str = Query("listing", pattern="^(listing|all)$"),
    # Flat by default: one folder of files is what an uploader wants to be pointed at, and the
    # SKU is already in every filename, so the folder-per-card only adds clicks.
    layout: str = Query("flat", pattern="^(flat|folders)$"),
    # Corner close-ups quadruple the file count and the upload time. Worth it on a card
    # somebody will zoom into, and dead weight on a bulk common.
    corners: bool = Query(True),
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
    import io
    import zipfile

    from app.enums import ImageKind
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

    # Ordered so the gallery image lands first in every uploader that sorts by name.
    ORDER = [
        (ImageKind.LISTING_FRONT, "1-front"),
        (ImageKind.LISTING_BACK, "2-back"),
    ]
    if corners:
        ORDER += [
            (ImageKind.DETAIL_FRONT_TL, "3-corner-top-left"),
            (ImageKind.DETAIL_FRONT_TR, "4-corner-top-right"),
            (ImageKind.DETAIL_FRONT_BL, "5-corner-bottom-left"),
            (ImageKind.DETAIL_FRONT_BR, "6-corner-bottom-right"),
        ]
    if kind == "all":
        ORDER += [
            (ImageKind.ORIGINAL_FRONT, "7-original-front"),
            (ImageKind.ORIGINAL_BACK, "8-original-back"),
        ]

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

    storage = get_storage()
    buffer = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        # ZIP_STORED, not DEFLATE: JPEGs are already compressed, so deflating them spends CPU
        # on a Pi to save almost nothing.
        for item in items:
            by_kind = {image.kind: image for image in item.images}
            for image_kind, label in ORDER:
                stored = by_kind.get(image_kind)
                if stored is None:
                    continue
                try:
                    name = f"{item.sku}-{label}.jpg"
                    archive.writestr(
                        name if layout == "flat" else f"{item.sku}/{name}",
                        storage.get(stored.path),
                    )
                    written += 1
                except Exception as exc:  # noqa: BLE001 - one missing file, not a failed zip
                    log.warning("photos_zip.skipped", sku=item.sku, error=str(exc))

    if written == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "No photographs in this batch yet — scan some cards, or wait for processing "
                "to finish."
            ),
        )

    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", target.name).strip("-") or "batch"
    log.info("photos_zip.built", session=str(session_id), files=written)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}-photos.zip"',
            "X-Photo-Count": str(written),
            "X-Card-Count": str(len(items)),
            "Access-Control-Expose-Headers": "X-Photo-Count, X-Card-Count",
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
