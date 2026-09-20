"""Health endpoints (REQ-OPS-001).

Liveness never touches a dependency — if the process is up, it answers. Readiness checks
everything the app needs to actually do work, so stopping Postgres flips readiness while
liveness stays green.
"""

import time

import redis.asyncio as aioredis
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config import settings
from app.db import new_session
from app.services import enum_drift
from app.storage import get_storage

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict:
    return {"status": "ok"}


async def _check_database() -> dict:
    started = time.perf_counter()
    try:
        async with new_session() as session:
            await session.execute(text("SELECT 1"))
        return {"ok": True, "ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 - a health check reports failures, never raises
        return {"ok": False, "error": str(exc)[:200]}


async def _check_redis() -> dict:
    started = time.perf_counter()
    client = None
    try:
        client = aioredis.from_url(settings.redis_url)
        await client.ping()
        return {"ok": True, "ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
    finally:
        if client:
            await client.aclose()


def _check_storage() -> dict:
    try:
        storage = get_storage()
        writable = getattr(storage, "writable", lambda: True)()
        return {"ok": bool(writable), "backend": settings.storage_backend}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


async def _check_enum_drift() -> list[str]:
    try:
        async with new_session() as session:
            return await enum_drift.check(session)
    except Exception as exc:  # noqa: BLE001 - health must never raise
        return [f"could not check: {exc}"]


@router.get("/health/detail")
async def detail() -> dict:
    checks = {
        "database": await _check_database(),
        "redis": await _check_redis(),
        "storage": _check_storage(),
    }
    return {
        "status": "ok" if all(c["ok"] for c in checks.values()) else "degraded",
        "checks": checks,
        "worker_tags": settings.tags,
        "display_currency": settings.display_currency,
        # The UI leads with scanning rather than listing when this is on.
        "scanner_mode": settings.scanner_mode,
        # Reported so the UI can shout about an instance running without a front door. An
        # unauthenticated deployment must never be the quiet state.
        "auth_required": settings.auth_required,
        "tls_port": settings.web_tls_port,
        # A stale process whose enums predate the schema fails on *read*, taking out every
        # endpoint touching the table rather than only the new rows. Surface it here.
        "enum_drift": await _check_enum_drift(),
    }


@router.get("/health/ready")
async def readiness(response: Response) -> dict:
    body = await detail()
    if body["status"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body
