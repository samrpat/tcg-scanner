"""arq worker.

Same image as the API, different command. Capability tags decide which queues this worker
serves, so moving recognition and condition work to the desktop is an environment change,
not a code change (docs/ARCHITECTURE.md).
"""

import uuid
from datetime import datetime

from arq.connections import RedisSettings
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import dispose_engine, new_session
from app.enums import ImageKind, JobStatus
from app.logging_setup import configure_logging, get_logger
from app.models import Image, InventoryItem, Job, ScanSession
from app.services.pricing import ingest_prices
from app.services.processing import process_image
from app.services.recognition import recognise_and_finish
from app.services.sync import sync_cards

configure_logging()
log = get_logger(__name__)


async def _mark(job_id: str, **values) -> None:
    async with new_session() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(**values))
        await session.commit()


async def _run(job_id: str, name: str, coro_factory) -> dict:
    await _mark(job_id, status=JobStatus.RUNNING, started_at=datetime.now().astimezone())
    log.info("job.start", job=name, job_id=job_id)
    try:
        result = await coro_factory()
    except Exception as exc:  # noqa: BLE001 - the failure is recorded, not swallowed
        log.error("job.failed", job=name, job_id=job_id, error=str(exc))
        await _mark(
            job_id,
            status=JobStatus.FAILED,
            error=str(exc)[:2000],
            finished_at=datetime.now().astimezone(),
        )
        raise
    await _mark(
        job_id,
        status=JobStatus.COMPLETE,
        result=result,
        finished_at=datetime.now().astimezone(),
    )
    counters = {k: v for k, v in result.items() if isinstance(v, int)}
    log.info("job.done", job=name, job_id=job_id, **counters)
    return result


async def sync_cards_task(ctx: dict, job_id: str, payload: dict) -> dict:
    async def work() -> dict:
        async with new_session() as session:
            async def progress(index: int, total: int, _set_id: str) -> None:
                await _mark(job_id, progress=index, progress_total=total)

            report = await sync_cards(
                session,
                set_ids=payload.get("set_ids"),
                refresh=bool(payload.get("refresh")),
                limit_sets=payload.get("limit_sets"),
                progress=progress,
            )
            return report.as_dict()

    return await _run(job_id, "sync_cards", work)


async def ingest_prices_task(ctx: dict, job_id: str, payload: dict) -> dict:
    async def work() -> dict:
        async with new_session() as session:
            return await ingest_prices(
                session, set_ids=payload.get("set_ids"), limit=payload.get("limit")
            )

    return await _run(job_id, "ingest_prices", work)


async def process_image_task(ctx: dict, image_id: str) -> dict:
    """Detect, rectify and assess one stored original.

    Not wrapped in `_run`: image processing is high-volume and per-capture, so mirroring every
    one into the `jobs` table would bury the handful of long-running sync jobs that table
    exists to make visible. Outcomes are recorded on the image and, when something is wrong,
    as a Review.
    """
    async with new_session() as session:
        result = await process_image(session, uuid.UUID(image_id))
        await session.commit()

        # Identification runs on its own the moment a usable front exists. Waiting for someone
        # to press a button is a per-card manual step, and the whole point of the pipeline is
        # that scanning is the only thing a human does.
        #
        # Gated on the front specifically: the back carries no identity, so a back-only capture
        # has nothing to recognise. Gated on not-yet-identified so that reprocessing a graded
        # card does not silently re-run recognition over it.
        if result.get("ok"):
            await _refresh_listing_images(session, uuid.UUID(image_id))
            await _maybe_recognise(ctx, session, uuid.UUID(image_id))
        return result


async def _refresh_listing_images(session, image_id: uuid.UUID) -> None:
    """Rebuild a card's listing renders after either side finishes processing.

    Listing images used to be built only as part of recognition, which fires when the *front*
    is processed. A back that finished afterwards therefore never got a listing render, and
    nothing ever went back for it: four of thirty cards ended up with a listing front and no
    listing back, while the progress bar reported the stage complete.

    That failure mode is the worst kind — silent, invisible to the operator, and not fixable by
    them. Building on every side means the second side to land always closes the gap, whichever
    order they arrive in.

    Idempotent: `build_for_item` overwrites in place, so running it twice costs one re-encode.
    """
    from app.services.listing_images import build_for_item

    image = await session.get(Image, image_id)
    if image is None:
        return
    item = (
        await session.execute(
            select(InventoryItem)
            .options(selectinload(InventoryItem.images))
            .where(InventoryItem.id == image.inventory_item_id)
        )
    ).scalar_one_or_none()
    if item is None:
        return
    try:
        await build_for_item(session, item)
        await session.commit()
    except Exception:  # noqa: BLE001 - a listing render must not fail the capture
        log.warning("worker.listing_images_failed", item=str(item.id))


async def _maybe_recognise(ctx: dict, session, image_id: uuid.UUID) -> None:
    image = await session.get(Image, image_id)
    if image is None or image.kind is not ImageKind.ORIGINAL_FRONT:
        return
    item = await session.get(InventoryItem, image.inventory_item_id)
    if item is None or item.card_id is not None:
        return

    # A photos-only batch is cropped and rendered but never identified: it exists to feed a
    # tool that does its own identification, and recognition here would be duplicated work
    # plus the two manual steps that cost the most time.
    if item.session_id is not None:
        batch = await session.get(ScanSession, item.session_id)
        if batch is not None and batch.photos_only:
            log.info("worker.recognition_skipped", sku=item.sku, reason="photos_only")
            return
    pool = ctx.get("redis")
    if pool is None:
        return
    try:
        await pool.enqueue_job("recognise_task", item.sku, str(item.user_id))
    except Exception:
        # A failed enqueue must not fail the capture; `make reconcile` catches stragglers.
        log.warning("worker.auto_recognise_enqueue_failed", sku=item.sku)


async def recognise_task(ctx: dict, sku: str, user_id: str) -> dict:
    """Identify the card an item holds. Runs on the `recognize` tag so the heavy feature
    matching can be pushed to a desktop worker while the Pi keeps capturing."""
    async with new_session() as session:
        result = await recognise_and_finish(session, sku, uuid.UUID(user_id))
        await session.commit()
        return result


async def on_startup(ctx: dict) -> None:
    log.info("worker.start", tags=settings.tags, concurrency=settings.worker_concurrency)


async def on_shutdown(ctx: dict) -> None:
    await dispose_engine()
    log.info("worker.stop")


# Only register tasks this worker is tagged to run. A desktop worker tagged
# `recognize,condition` will not pick up ingest jobs, and vice versa.
_ALL_TASKS = {
    "ingest": [sync_cards_task, ingest_prices_task, process_image_task],
    "recognize": [recognise_task],
}


def _functions() -> list:
    functions: list = []
    for tag, tasks in _ALL_TASKS.items():
        if tag in settings.tags:
            functions.extend(tasks)
    return functions


class WorkerSettings:
    functions = _functions()
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings(host=settings.redis_host, port=settings.redis_port)
    max_jobs = settings.worker_concurrency
    # A full catalogue sync is long-running by nature; do not let arq time it out.
    job_timeout = 60 * 60 * 6
    keep_result = 60 * 60
