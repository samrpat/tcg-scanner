"""FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import dispose_engine
from app.logging_setup import configure_logging, get_logger
from app.routers import (
    capture,
    catalog,
    conditioning,
    ebay,
    health,
    images,
    inventory,
    jobs,
    review,
    sessions,
)

configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("api.start", storage=settings.storage_backend, tags=settings.tags)
    yield
    await dispose_engine()
    log.info("api.stop")


app = FastAPI(
    title="TCG Scanner API",
    version="0.1.0",
    summary="Self-hosted Pokémon TCG scanner, inventory and marketplace platform.",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

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
for module in (catalog, conditioning, capture, jobs, images, review, inventory, ebay, sessions):
    app.include_router(module.router, prefix="/api")


@app.get("/api")
async def root() -> dict:
    return {
        "name": "TCG Scanner API",
        "version": "0.1.0",
        "phase": 2,
        "docs": "/api/docs",
    }
