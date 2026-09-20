"""The gate every request passes through.

Middleware rather than a dependency on each route, and that choice is the security property:
a dependency has to be remembered, and the day someone adds a router without it, that router is
public. Two of them already were — `/api/images`, which serves every photograph of every card,
and `/api/catalog` — because they had no reason to load a user and so never asked for one.

The allowlist below is the whole of what an unauthenticated caller can reach.
"""

from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import new_session
from app.logging_setup import get_logger
from app.services import auth as auth_service

log = get_logger(__name__)

# Reachable without a session.
#
# `/health` is here so a container healthcheck does not need a credential — it reports whether
# the process is alive and its dependencies answer, and nothing about the collection.
# `/api/auth` is here because it is the way in.
OPEN_PREFIXES = ("/health", "/api/auth")

# Token -> (user id, when it was checked). One indexed lookup per request is cheap, but a batch
# screen loads twenty images at once and every one of them is a request; this turns that into
# one query. Deliberately short, so a revoked device stops working within the window even in a
# deployment where this cache is per-process and revocation only clears one of them.
_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 30.0

# Whether the operator chose, at first run, to run without a password. Cached for the same
# reason and read only when a request has no session — which on an open instance is every
# request, hence the cache, and on a closed one is only the refusals.
_OPEN_INSTANCE: tuple[bool, float] | None = None


def forget(token: str) -> None:
    _CACHE.pop(token, None)


def forget_all() -> None:
    global _OPEN_INSTANCE
    _CACHE.clear()
    _OPEN_INSTANCE = None


def _cached(token: str) -> str | None:
    hit = _CACHE.get(token)
    if hit is None:
        return None
    user_id, checked = hit
    if time.monotonic() - checked > _CACHE_TTL:
        _CACHE.pop(token, None)
        return None
    return user_id


def is_open(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in OPEN_PREFIXES)


async def authenticate(request: Request) -> None:
    """Resolve the cookie onto `request.state.user_id`, or leave it unset."""
    request.state.user_id = None
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return

    cached = _cached(token)
    if cached is not None:
        request.state.user_id = cached
        return

    async with new_session() as session:
        user = await auth_service.user_for_token(session, token)
        await session.commit()
    if user is not None:
        request.state.user_id = str(user.id)
        _CACHE[token] = (str(user.id), time.monotonic())


async def instance_is_open() -> bool:
    """Whether this instance was set up without a password.

    A deliberate choice made at first run, stored in the database rather than the environment
    so it can be made — and reversed — from the screen rather than from a file and a restart.
    """
    global _OPEN_INSTANCE
    if _OPEN_INSTANCE is not None:
        value, checked = _OPEN_INSTANCE
        if time.monotonic() - checked <= _CACHE_TTL:
            return value

    from sqlalchemy import select

    from app.models import User
    from app.services.seed import DEFAULT_USER_EMAIL

    try:
        async with new_session() as session:
            user = (
                await session.execute(select(User).where(User.email == DEFAULT_USER_EMAIL))
            ).scalar_one_or_none()
            # An unclaimed instance is not an open one. "No password yet" means the choice has
            # not been made, and the safe reading of an unanswered question is no.
            value = bool(user and user.auth_disabled)
    except Exception:  # noqa: BLE001 - a database that cannot answer does not mean "let them in"
        return False

    _OPEN_INSTANCE = (value, time.monotonic())
    return value


async def middleware(request: Request, call_next):
    """Authenticate, then refuse anything outside the allowlist without a session."""
    await authenticate(request)

    if not settings.auth_required or is_open(request.url.path):
        return await call_next(request)

    if request.state.user_id is not None:
        return await call_next(request)

    if await instance_is_open():
        return await call_next(request)

    # Refused without touching the database, deliberately.
    #
    # This used to look up whether a password had ever been set, so the body could say which
    # kind of "no" it was. That put a query on the rejection path: the cheapest request to make
    # against this service became the one that hit Postgres, and a database outage turned every
    # 401 into a 500. `/api/auth/status` answers that question already, and the UI asks it — so
    # the gate can just be a gate.
    return JSONResponse(
        status_code=401,
        content={"detail": "log in first", "reason": "unauthenticated"},
    )
