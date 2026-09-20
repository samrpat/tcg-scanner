"""FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.authz import middleware as auth_middleware
from app.config import settings
from app.db import dispose_engine
from app.logging_setup import configure_logging, get_logger
from app.routers import (
    auth,
    capture,
    catalog,
    conditioning,
    ebay,
    health,
    images,
    inventory,
    jobs,
    preferences,
    review,
    sessions,
)

configure_logging()
log = get_logger(__name__)

# Scanning: capture, crop, corner detail, batches, the photo download. Everything the scanner
# build exists for, and everything the full app needs too.
SCANNER_ROUTERS = (capture, jobs, images, inventory, sessions, catalog, preferences)

# Grading, approval, pricing and selling. A scanner build hides all of these screens, and the
# reason not to mount them anyway is not the handful of megabytes: this app has no
# authentication, so every mounted route — including the ones that delete things and the ones
# that publish photographs to the internet — is open to anything that can reach the port.
# Mounting only what the running mode actually uses keeps that surface honest.
LISTING_ROUTERS = (conditioning, review, ebay)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("api.start", storage=settings.storage_backend, tags=settings.tags)
    # Read once, here, so the request path never has to. See app/authz.py.
    from app.authz import refresh_instance_flag

    log.info("api.instance_open", value=await refresh_instance_flag())
    yield
    await dispose_engine()
    log.info("api.stop")


def build_app(scanner: bool | None = None, auth_required: bool | None = None) -> FastAPI:
    """Assemble the API for one mode.

    A factory rather than a module-level app so that the mode is an argument. Tests exercise
    the listing endpoints regardless of how the container they run in happens to be
    configured, which they could not do when the router set was decided by an environment
    variable read at import time.

    `auth_required` exists for the same reason. It defaults to the setting, and a test pins
    that default on — a switch that silently defaulted off would be worse than no switch.
    """
    scanner = settings.scanner_mode if scanner is None else scanner
    auth_required = settings.auth_required if auth_required is None else auth_required

    app = FastAPI(
        title="TCG Scanner API",
        version="0.1.0",
        summary="Self-hosted Pokémon TCG scanner, inventory and marketplace platform.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # Ahead of CORS in the decorator order, which means it runs *after* it: a preflight is
    # answered without a credential, and the real request behind it is not.
    if auth_required:
        app.middleware("http")(auth_middleware)

    if settings.origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Health lives at the root so container healthchecks do not depend on the /api prefix.
    app.include_router(health.router)
    # The way in. Reachable without a session; see `app.authz.OPEN_PREFIXES`.
    app.include_router(auth.router, prefix="/api")
    for module in SCANNER_ROUTERS:
        app.include_router(module.router, prefix="/api")
    if not scanner:
        for module in LISTING_ROUTERS:
            app.include_router(module.router, prefix="/api")

    @app.get("/api")
    async def root() -> dict:
        return {
            "name": "TCG Scanner API",
            "version": "0.1.0",
            "phase": 2,
            "mode": "scanner" if scanner else "full",
            "docs": "/api/docs",
        }

    log.info(
        "api.routers",
        mode="scanner" if scanner else "full",
        listing_routes=not scanner,
        auth=auth_required,
    )
    if not auth_required:
        log.warning(
            "api.auth_disabled",
            detail="AUTH_REQUIRED is off — every endpoint is open to anything that can "
            "reach this port, including the ones that delete inventory",
        )
    return app


app = build_app()
