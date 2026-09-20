"""Enqueue background work and read its status.

The API never runs a long job inline — it enqueues and returns immediately, which is what
keeps the capture loop inside its 36-second budget (D-005).
"""

import uuid

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.enums import JobStatus
from app.models import Job, User
from app.routers.capture import current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])


class SyncRequest(BaseModel):
    set_ids: list[str] | None = None
    refresh: bool = False
    limit_sets: int | None = None


class PriceRequest(BaseModel):
    set_ids: list[str] | None = None
    limit: int | None = None


async def _enqueue(session: AsyncSession, task: str, tag: str, payload: dict) -> Job:
    job = Job(type=task, tag=tag, payload=payload, status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()
    await session.refresh(job)

    pool = await create_pool(RedisSettings(host=settings.redis_host, port=settings.redis_port))
    try:
        enqueued = await pool.enqueue_job(task, str(job.id), payload)
        if enqueued is not None:
            job.arq_job_id = enqueued.job_id
            await session.commit()
    finally:
        await pool.aclose()
    return job


@router.post("/sync")
async def start_sync(body: SyncRequest, session: AsyncSession = Depends(get_session)) -> dict:
    job = await _enqueue(session, "sync_cards_task", "ingest", body.model_dump())
    return {"job_id": str(job.id), "status": job.status.value}


@router.post("/prices")
async def start_prices(body: PriceRequest, session: AsyncSession = Depends(get_session)) -> dict:
    job = await _enqueue(session, "ingest_prices_task", "ingest", body.model_dump())
    return {"job_id": str(job.id), "status": job.status.value}


@router.get("")
async def list_jobs(limit: int = 25, session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (
        await session.execute(select(Job).order_by(desc(Job.created_at)).limit(limit))
    ).scalars()
    return [_job_dict(j) for j in rows]


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_dict(job)


def _job_dict(job: Job) -> dict:
    return {
        "id": str(job.id),
        "type": job.type,
        "tag": job.tag,
        "status": job.status.value,
        "progress": job.progress,
        "progress_total": job.progress_total,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


class EbaySoldIn(BaseModel):
    skus: list[str]
    max_results: int | None = None


@router.post("/ebay-sold")
async def fetch_ebay_sold(
    body: EbaySoldIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Fetch real eBay sold prices for the selected cards.

    Selection-driven and never automatic, because each result costs money. The caller picks the
    cards worth the spend — which for a bulk collection is the handful above the bulk threshold,
    not all two thousand.
    """
    from app.services.ebay_sold_job import fetch_for_skus

    if not body.skus:
        raise HTTPException(status_code=422, detail="select some cards first")
    if len(body.skus) > 200:
        raise HTTPException(
            status_code=422,
            detail="that is a lot of paid lookups at once — select 200 cards or fewer",
        )
    return await fetch_for_skus(session, user, body.skus, body.max_results)
