"""Logging in.

The whole of the front door. Three states, and the API behaves differently in each:

- **Unclaimed** — no password has ever been set. Only these routes answer; everything else is
  401 with an instruction. The UI offers to choose a password. This is the first-run state and
  it is deliberately not "let everyone in", which is what no-password used to mean.
- **Claimed, no cookie** — same, but the UI offers to log in.
- **Claimed, valid cookie** — the application, as before.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import authz
from app.config import settings
from app.db import get_session
from app.logging_setup import get_logger
from app.models import AuthSession, User
from app.services import auth as auth_service
from app.services.seed import DEFAULT_USER_EMAIL

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger(__name__)


class PasswordIn(BaseModel):
    password: str
    # Shown in the device list so one can be told from another when revoking.
    label: str | None = None


class ChangeIn(BaseModel):
    current: str
    new: str


async def _the_user(session: AsyncSession) -> User | None:
    return (
        await session.execute(select(User).where(User.email == DEFAULT_USER_EMAIL))
    ).scalar_one_or_none()


def _set_cookie(response: Response, request: Request, token: str) -> None:
    """The session cookie.

    HttpOnly so a script cannot read it, SameSite=Lax so it rides along with ordinary
    navigation and image loads but not with a cross-site form post, and Secure only when the
    request actually arrived over TLS — marking it Secure on the plain-HTTP LAN address would
    mean the browser silently never sends it back, which presents as "logging in does nothing".
    """
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=settings.auth_session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


# ── rate limiting ──────────────────────────────────────────────────────────────────────────


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _too_many(request: Request) -> bool:
    """Whether this address has failed too often lately.

    Redis-backed, and **fails open**: if Redis is unreachable the login is still checked
    against the password. Locking the operator out of their own scanner because the queue is
    down trades a real outage for a theoretical one.
    """
    try:
        import redis.asyncio as aioredis

        client = aioredis.Redis(host=settings.redis_host, port=settings.redis_port)
        try:
            count = await client.get(f"auth:fail:{_client(request)}")
            return int(count or 0) >= settings.auth_max_failures
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001 - see docstring
        return False


async def _record_failure(request: Request) -> None:
    try:
        import redis.asyncio as aioredis

        client = aioredis.Redis(host=settings.redis_host, port=settings.redis_port)
        try:
            key = f"auth:fail:{_client(request)}"
            await client.incr(key)
            await client.expire(key, settings.auth_lockout_seconds)
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001 - a missing counter must not fail the request
        pass


async def _clear_failures(request: Request) -> None:
    try:
        import redis.asyncio as aioredis

        client = aioredis.Redis(host=settings.redis_host, port=settings.redis_port)
        try:
            await client.delete(f"auth:fail:{_client(request)}")
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001
        pass


# ── routes ─────────────────────────────────────────────────────────────────────────────────


@router.get("/status")
async def status(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """What the UI needs to decide which screen to show. Always answers, never 401s."""
    user = await _the_user(session)
    return {
        "required": settings.auth_required,
        "claimed": auth_service.is_claimed(user),
        "authenticated": getattr(request.state, "user_id", None) is not None,
        "min_password_length": auth_service.MIN_PASSWORD_LENGTH,
    }


@router.post("/claim")
async def claim(
    body: PasswordIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Set the first password, on an instance that has never had one, and log in.

    Refuses once a password exists — otherwise it would be a way to take over an instance
    without knowing the current password, which is the opposite of the point.
    """
    user = await _the_user(session)
    if user is None:
        raise HTTPException(status_code=503, detail="no user seeded; run `make seed`")
    if auth_service.is_claimed(user):
        raise HTTPException(
            status_code=409,
            detail="this instance already has a password — log in, or reset it with "
            "`make set-password`",
        )

    try:
        await auth_service.set_password(session, user, body.password)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    token = await auth_service.issue_session(
        session, user, days=settings.auth_session_days, label=body.label
    )
    await session.commit()
    _set_cookie(response, request, token)
    log.info("auth.claimed")
    return {"claimed": True}


@router.post("/login")
async def login(
    body: PasswordIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await _the_user(session)
    if not auth_service.is_claimed(user):
        raise HTTPException(
            status_code=409, detail="no password set yet — choose one to claim this instance"
        )

    if await _too_many(request):
        raise HTTPException(
            status_code=429,
            detail="too many failed attempts — wait "
            f"{settings.auth_lockout_seconds // 60} minutes",
        )

    assert user is not None  # is_claimed established it
    if not auth_service.verify_password(body.password, user.password_hash):
        await _record_failure(request)
        log.warning("auth.login_failed", client=_client(request))
        raise HTTPException(status_code=401, detail="wrong password")

    await _clear_failures(request)
    token = await auth_service.issue_session(
        session, user, days=settings.auth_session_days, label=body.label
    )
    await session.commit()
    _set_cookie(response, request, token)
    return {"authenticated": True, "days": settings.auth_session_days}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    token = request.cookies.get(settings.auth_cookie_name, "")
    removed = await auth_service.revoke_token(session, token) if token else False
    await session.commit()
    authz.forget(token)
    response.delete_cookie(settings.auth_cookie_name, path="/")
    return {"logged_out": removed}


@router.post("/password")
async def change_password(
    body: ChangeIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Change the password, and sign every other device out.

    Signing the others out is the point of changing it. A password change that leaves the
    device you are worried about still logged in has not done anything.
    """
    user = await _the_user(session)
    if user is None or not auth_service.is_claimed(user):
        raise HTTPException(status_code=409, detail="no password set yet")
    if getattr(request.state, "user_id", None) is None:
        raise HTTPException(status_code=401, detail="log in first")
    if not auth_service.verify_password(body.current, user.password_hash):
        raise HTTPException(status_code=401, detail="wrong password")

    try:
        await auth_service.set_password(session, user, body.new)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    keep = request.cookies.get(settings.auth_cookie_name, "")
    signed_out = await auth_service.revoke_all(session, user, keep=keep)
    await session.commit()
    # Blunt, and correct: the point of a password change is that the other devices stop
    # working *now*, not when a cache entry happens to age out.
    authz.forget_all()
    return {"changed": True, "other_devices_signed_out": signed_out}


@router.get("/devices")
async def devices(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Every logged-in device, so a lost phone can be found and revoked."""
    user = await _the_user(session)
    if user is None or getattr(request.state, "user_id", None) is None:
        raise HTTPException(status_code=401, detail="log in first")

    mine = auth_service.token_hash(request.cookies.get(settings.auth_cookie_name, ""))
    rows = (
        (
            await session.execute(
                select(AuthSession)
                .where(AuthSession.user_id == user.id)
                .order_by(AuthSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "devices": [
            {
                "id": str(row.id),
                "label": row.label,
                "created_at": row.created_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "expires_at": row.expires_at.isoformat(),
                "this_one": row.token_hash == mine,
            }
            for row in rows
        ]
    }


@router.delete("/devices/{device_id}")
async def revoke_device(
    device_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await _the_user(session)
    if user is None or getattr(request.state, "user_id", None) is None:
        raise HTTPException(status_code=401, detail="log in first")
    if not await auth_service.revoke_one(session, user, device_id):
        raise HTTPException(status_code=404, detail="no such device")
    await session.commit()
    authz.forget_all()
    return {"revoked": str(device_id)}


@router.post("/purge")
async def purge(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Drop timed-out sessions. Expiry is enforced on every read regardless; this only keeps
    the table from growing a row per login for ever."""
    if getattr(request.state, "user_id", None) is None:
        raise HTTPException(status_code=401, detail="log in first")
    removed = await auth_service.purge_expired(session)
    await session.commit()
    return {"removed": removed, "at": datetime.now(UTC).isoformat()}
