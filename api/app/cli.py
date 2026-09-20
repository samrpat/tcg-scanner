"""Command line entry points, wrapped by the Makefile.

    python -m app.cli seed
    python -m app.cli sync [--set base1] [--refresh] [--limit-sets 3]
    python -m app.cli prices [--set base1] [--limit 100]
    python -m app.cli stats
"""

import argparse
import asyncio
import json
import sys

from sqlalchemy import func, select

from app.db import dispose_engine, new_session
from app.logging_setup import configure_logging
from app.models import Card, CardSet, CardVariant, Price
from app.services.pricing import ingest_prices
from app.services.seed import seed_all
from app.services.sync import sync_cards


async def _seed(args) -> dict:
    async with new_session() as session:
        return await seed_all(session, reset=args.reset)


async def _sync(args) -> dict:
    async with new_session() as session:
        report = await sync_cards(
            session,
            set_ids=args.set or None,
            refresh=args.refresh,
            limit_sets=args.limit_sets,
        )
        return report.as_dict()


async def _prices(args) -> dict:
    async with new_session() as session:
        return await ingest_prices(
            session,
            set_ids=args.set or None,
            limit=args.limit,
            owned_only=not args.all,
            stale_after_days=None if args.refresh else args.stale_after,
        )


async def _mark(args) -> dict:
    from sqlalchemy import update

    from app.models import InventoryItem

    async with new_session() as session:
        result = await session.execute(
            update(InventoryItem)
            .where(InventoryItem.sku.in_(args.sku))
            .values(contains_card=args.contains_card)
        )
        await session.commit()
        return {"updated": result.rowcount, "contains_card": args.contains_card}


async def _index_art(args) -> dict:
    from app.services.art_index import build_index

    async with new_session() as session:
        return await build_index(
            session, limit=args.limit, set_id=args.set_id, refresh=args.refresh
        )


async def _warm_art(args) -> dict:
    from app.services.art_cache import warm

    async with new_session() as session:
        return await warm(session, set_id=args.set_id, limit=args.limit)


async def _back_reference(args) -> dict:
    from app.services.back_reference import rebuild

    async with new_session() as session:
        return await rebuild(session, dry_run=args.dry_run)


async def _listings(args) -> dict:
    from sqlalchemy import select

    from app.models import InventoryItem
    from app.services.listing_images import build_for_item

    async with new_session() as session:
        items = (
            await session.execute(select(InventoryItem).order_by(InventoryItem.sku))
        ).scalars().all()
        rendered = 0
        for item in items:
            result = await build_for_item(session, item, margin_mm=args.margin)
            rendered += len(result["rendered"])
        await session.commit()
        return {"cards": len(items), "images": rendered, "margin_mm": args.margin or "config"}


async def _reconcile() -> dict:
    """Find and repair cards the pipeline left half-finished.

    Two distinct failures, because they have different causes and different fixes:

    - a *lost job*: the original was stored but never processed, so it is re-queued;
    - an *incomplete render*: the sides processed fine but a listing image is missing, which no
      amount of re-processing fixes because nothing was ever wrong with the original.

    Both are silent, and a silent gap is the expensive kind: the card sits looking finished,
    cannot proceed to grading or listing, and the operator has no way to find it.
    """
    from arq import create_pool
    from arq.connections import RedisSettings
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.config import settings
    from app.models import InventoryItem
    from app.services.listing_images import build_for_item
    from app.services.processing import find_incomplete_renders, find_unprocessed

    async with new_session() as session:
        stale = await find_unprocessed(session)
        incomplete = await find_incomplete_renders(session)

        if stale:
            pool = await create_pool(
                RedisSettings(host=settings.redis_host, port=settings.redis_port)
            )
            try:
                for image in stale:
                    await pool.enqueue_job("process_image_task", str(image.id))
            finally:
                await pool.aclose()

        repaired: list[str] = []
        failed: list[str] = []
        for item in incomplete:
            full = (
                await session.execute(
                    select(InventoryItem)
                    .options(selectinload(InventoryItem.images))
                    .where(InventoryItem.id == item.id)
                )
            ).scalar_one_or_none()
            if full is None:
                continue
            try:
                await build_for_item(session, full)
                await session.commit()
                repaired.append(full.sku)
            except Exception as exc:  # noqa: BLE001 - one bad card must not stop the repair
                failed.append(f"{full.sku}: {exc}")

        return {
            "unprocessed": len(stale),
            "requeued": len(stale),
            "images": [i.path for i in stale[:20]],
            "incomplete_renders": len(incomplete),
            "repaired": repaired[:20],
            "failed": failed[:20],
        }


async def _benchmark() -> dict:
    from app.services import benchmark

    async with new_session() as session:
        return await benchmark.run(session)


async def _stats() -> dict:
    async with new_session() as session:
        async def count(model) -> int:
            return (await session.execute(select(func.count()).select_from(model))).scalar_one()

        return {
            "sets": await count(CardSet),
            "cards": await count(Card),
            "variants": await count(CardVariant),
            "price_observations": await count(Price),
        }


async def _scrub_metadata(args) -> dict:
    """Clean originals that were stored before uploads were scrubbed on the way in.

    New captures need none of this — `attach_image` scrubs everything it writes. This is for a
    collection that already exists, where an upload from a camera roll may be sitting on disk
    complete with the coordinates of the room it was taken in.

    Lossless, so it is safe to run over a whole collection: the metadata segments come out and
    the compressed picture is copied through untouched.
    """
    from sqlalchemy import select

    from app.enums import ImageKind
    from app.models import Image as ImageRow
    from app.services.scrub import scrub_jpeg
    from app.storage import get_storage

    storage = get_storage()
    originals = (ImageKind.ORIGINAL_FRONT, ImageKind.ORIGINAL_BACK)

    cleaned, untouched, failed = [], 0, []
    async with new_session() as session:
        rows = (
            (await session.execute(select(ImageRow).where(ImageRow.kind.in_(originals))))
            .scalars()
            .all()
        )
        for row in rows:
            try:
                payload = storage.get(row.path)
            except Exception:  # noqa: BLE001 - a missing file is not this command's problem
                failed.append(row.path)
                continue

            scrubbed, removed = scrub_jpeg(payload)
            if not removed or scrubbed == payload:
                untouched += 1
                continue

            cleaned.append({"path": row.path, "removed": removed,
                            "bytes_saved": len(payload) - len(scrubbed)})
            if not args.dry_run:
                stored = storage.put(row.path, scrubbed, overwrite=True)
                # The hash is what image URLs are versioned on, so it has to follow.
                row.sha256 = stored.sha256
                row.bytes = stored.bytes
        if not args.dry_run:
            await session.commit()

    return {
        "examined": len(rows),
        "cleaned": len(cleaned),
        "already_clean": untouched,
        "unreadable": failed,
        "dry_run": bool(args.dry_run),
        "examples": cleaned[:5],
    }


async def _set_password(args) -> dict:
    """Set the password from the command line.

    The recovery path. The UI can set a password on an instance that has never had one and
    change a password you know, and neither helps once it has been forgotten — at which point
    the person at the keyboard has shell access to the box anyway, so this is not a weaker
    door than the one it opens.
    """
    import getpass

    from sqlalchemy import select

    from app.models import User
    from app.services import auth as auth_service
    from app.services.seed import DEFAULT_USER_EMAIL

    password = args.password
    if not password:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Again: "):
            raise SystemExit("they did not match")

    async with new_session() as session:
        user = (
            await session.execute(select(User).where(User.email == DEFAULT_USER_EMAIL))
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit("no user seeded; run `make seed` first")

        try:
            await auth_service.set_password(session, user, password)
        except auth_service.AuthError as exc:
            raise SystemExit(str(exc)) from exc

        signed_out = 0
        if args.sign_out_everything:
            signed_out = await auth_service.revoke_all(session, user)
        await session.commit()

    return {
        "password_set": True,
        "devices_signed_out": signed_out,
        "note": "restart the api if it is running, so its session cache is dropped"
        if signed_out
        else None,
    }


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="Seed reference data. Idempotent.")
    p_seed.add_argument(
        "--reset",
        action="store_true",
        help="Re-apply shipped defaults over existing marketplace translations",
    )

    p_sync = sub.add_parser("sync", help="Sync card data from TCGdex.")
    p_sync.add_argument("--set", action="append", help="TCGdex set id; repeatable")
    p_sync.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch and rewrite cards already stored, even if unchanged upstream",
    )
    p_sync.add_argument("--limit-sets", type=int, default=None)

    p_prices = sub.add_parser("prices", help="Ingest prices from TCGdex.")
    p_prices.add_argument("--set", action="append", help="TCGdex set id; repeatable")
    p_prices.add_argument("--limit", type=int, default=None)
    p_prices.add_argument(
        "--all", action="store_true",
        help="Price the whole catalogue, not just cards in inventory (23k+ requests).",
    )
    p_prices.add_argument(
        "--refresh", action="store_true", help="Re-price even cards priced recently."
    )
    p_prices.add_argument(
        "--stale-after", dest="stale_after", type=int, default=7,
        help="Skip cards priced within this many days (default 7).",
    )

    sub.add_parser("stats", help="Row counts.")

    p_mark = sub.add_parser(
        "mark",
        help="Label whether a capture contains a card, for honest benchmarking.",
    )
    p_mark.add_argument("sku", nargs="+")
    p_mark.add_argument(
        "--no-card",
        dest="contains_card",
        action="store_false",
        default=True,
        help="This capture deliberately contains no card",
    )

    p_index = sub.add_parser(
        "index-art",
        help="Compute perceptual hashes for card art. Resumable.",
    )
    p_index.add_argument("--limit", type=int)
    p_index.add_argument("--set", dest="set_id")
    p_index.add_argument("--refresh", action="store_true", help="Re-hash cards already done")

    p_warm = sub.add_parser(
        "warm-art",
        help="Pre-download reference art so recognition never waits on the network.",
    )
    p_warm.add_argument("--set", dest="set_id")
    p_warm.add_argument("--limit", type=int)

    p_back = sub.add_parser(
        "back-reference",
        help="Rebuild the card-back reference by consensus from stored captures.",
    )
    p_back.add_argument("--dry-run", action="store_true")

    p_listings = sub.add_parser(
        "listings",
        help="Re-render every listing image, optionally at a different margin.",
    )
    p_listings.add_argument(
        "--margin", type=float, help="Spacing around the card in mm (default from config)"
    )

    sub.add_parser(
        "reconcile",
        help="Find captures whose processing job was lost, and re-queue them.",
    )

    sub.add_parser(
        "benchmark",
        help="Run detection over every stored original and report how it did.",
    )

    p_scrub = sub.add_parser(
        "scrub-metadata",
        help="Strip location and device metadata from originals already stored.",
    )
    p_scrub.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed without writing anything.",
    )

    p_password = sub.add_parser(
        "set-password",
        help="Set the login password. The way back in when it has been forgotten.",
    )
    p_password.add_argument(
        "--password",
        help="Read from a prompt when omitted, which keeps it out of the shell history.",
    )
    p_password.add_argument(
        "--sign-out-everything",
        action="store_true",
        help="Revoke every logged-in device as well. What you want if one has been lost.",
    )

    args = parser.parse_args()

    async def run() -> dict:
        try:
            if args.command == "seed":
                return await _seed(args)
            if args.command == "sync":
                return await _sync(args)
            if args.command == "prices":
                return await _prices(args)
            if args.command == "mark":
                return await _mark(args)
            if args.command == "index-art":
                return await _index_art(args)
            if args.command == "warm-art":
                return await _warm_art(args)
            if args.command == "back-reference":
                return await _back_reference(args)
            if args.command == "listings":
                return await _listings(args)
            if args.command == "reconcile":
                return await _reconcile()
            if args.command == "benchmark":
                return await _benchmark()
            if args.command == "set-password":
                return await _set_password(args)
            if args.command == "scrub-metadata":
                return await _scrub_metadata(args)
            return await _stats()
        finally:
            await dispose_engine()

    result = asyncio.run(run())
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
