"""Settings, and what this install is.

Two different things behind one screen, and keeping them apart matters:

- **Preferences** live on the user row and can be changed here. They are the ones where
  changing them means something at runtime.
- **Configuration** comes from the environment — the mode, whether there is a password, where
  images are stored. It is reported read-only with a note saying where it is actually set,
  because a settings screen that appears to offer a switch it cannot throw is worse than one
  that explains the switch is elsewhere.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as env
from app.db import get_session
from app.logging_setup import get_logger
from app.models import Image, InventoryItem, ScanSession, User
from app.routers.capture import current_user

router = APIRouter(prefix="/settings", tags=["settings"])
log = get_logger(__name__)

# Everything the UI may store. An allowlist rather than a free-form blob: this column is
# written straight from the browser, and "whatever the client sent" is how a settings object
# becomes a place to stash unbounded junk.
ALLOWED = {
    # Whether the introduction has been seen. Server-side, so it does not reappear on the
    # phone after being dismissed on the desktop.
    "intro_done": bool,
    # Which step it was left on, so closing it half way does not start again from the top.
    "intro_step": int,
    # Default for new batches. The per-batch switch still wins.
    "corner_shots_default": bool,
}

DEFAULTS = {"intro_done": False, "intro_step": 0, "corner_shots_default": True}


class PreferencesIn(BaseModel):
    intro_done: bool | None = None
    intro_step: int | None = None
    corner_shots_default: bool | None = None


def _merged(user: User) -> dict:
    stored = user.settings or {}
    return {key: stored.get(key, fallback) for key, fallback in DEFAULTS.items()}


@router.get("")
async def read_preferences(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Preferences, plus what this install is and how much is in it."""
    cards = (
        await session.execute(
            select(func.count(InventoryItem.id)).where(InventoryItem.user_id == user.id)
        )
    ).scalar_one()
    batches = (
        await session.execute(
            select(func.count(ScanSession.id)).where(ScanSession.user_id == user.id)
        )
    ).scalar_one()
    photographs = (
        await session.execute(
            select(func.count(Image.id)).join(
                InventoryItem, Image.inventory_item_id == InventoryItem.id
            ).where(InventoryItem.user_id == user.id)
        )
    ).scalar_one()

    return {
        "preferences": _merged(user),
        # Read-only. Each line says where it is actually set, so the screen never looks like
        # it is offering a switch it cannot throw.
        "configuration": {
            "mode": "scanner" if env.scanner_mode else "full",
            "mode_source": "SCANNER_MODE in .env — restart to change",
            "authentication": "on" if env.auth_required else "OFF",
            "authentication_source": "AUTH_REQUIRED in .env — restart to change",
            "session_days": env.auth_session_days,
            "storage": env.storage_backend,
            "listing_px_per_mm": env.listing_px_per_mm,
            "https_port": env.web_tls_port,
        },
        "collection": {
            "cards": cards,
            "batches": batches,
            "photographs": photographs,
        },
        "version": env.version,
    }


# PATCH, not PUT: the body names only what is changing, and everything else is left alone.
@router.patch("")
async def write_preferences(
    body: PreferencesIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Change preferences. Only the named keys, and only where a value was given."""
    stored = dict(user.settings or {})
    for key in ALLOWED:
        value = getattr(body, key, None)
        if value is not None:
            stored[key] = value
    user.settings = stored
    await session.commit()
    log.info("settings.updated", keys=sorted(k for k in ALLOWED if getattr(body, k) is not None))
    return {"preferences": _merged(user)}
