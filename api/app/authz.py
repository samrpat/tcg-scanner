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


def forget(token: str) -> None:
    _CACHE.pop(token, None)


def forget_all() -> None:
    _CACHE.clear()


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


async def middleware(request: Request, call_next):
    """Authenticate, then refuse anything outside the allowlist without a session."""
    await authenticate(request)

    if not settings.auth_required or is_open(request.url.path):
        return await call_next(request)

    if request.state.user_id is not None:
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
