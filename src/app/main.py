# src/app/main.py
"""
FastAPI application entry‑point.

- Registers API routers.
- Starts an APScheduler background job that polls the StackExchange “SN” feed.
- Manages database lifecycle (connect / disconnect) on startup/shutdown.
- Provides structured logging and graceful error handling.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Coroutine

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseSettings, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env file."""

    # FastAPI
    title: str = Field("SN Bounty Service", env="APP_TITLE")
    version: str = Field("0.1.0", env="APP_VERSION")
    debug: bool = Field(False, env="APP_DEBUG")

    # Database
    database_url: str = Field(
        "sqlite+aiosqlite:///./bounties.db", env="DATABASE_URL"
    )

    # Scheduler
    poll_interval_seconds: int = Field(300, env="POLL_INTERVAL_SECONDS")
    sn_feed_url: str = Field(
        "https://stackexchange.com/feeds/sn.csv", env="SN_FEED_URL"
    )

    # HTTP client
    http_timeout: int = Field(30, env="HTTP_TIMEOUT")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("sn_bounty_service")

# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
engine: AsyncEngine = create_async_engine(
    settings.database_url, echo=False, future=True
)
AsyncSessionLocal = sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields a database session."""
    async with AsyncSessionLocal() as session:
        yield session


# --------------------------------------------------------------------------- #
# Scheduler & Background Job
# --------------------------------------------------------------------------- #
scheduler = AsyncIOScheduler()


async def fetch_and_process_feed() -> None:
    """
    Fetches the SN CSV feed, parses rows, and stores new OPEN_BOUNTY entries.
    Errors are logged but do not stop the scheduler.
    """
    logger.info("Polling SN feed from %s", settings.sn_feed_url)
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        try:
            response = await client.get(settings.sn_feed_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch SN feed: %s", exc)
            return

    # Simple CSV parsing – real implementation would be more robust
    for line in response.text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        try:
            fields = line.split("\t")
            # Expected format (example):
            # id, site, type, score, bounty, ... (adjust indices as needed)
            bounty_type = fields[2].strip()
            if bounty_type != "OPEN_BOUNTY":
                continue
            # Insert into DB – placeholder logic (replace with repository call)
            async with AsyncSessionLocal() as session:
                # Example: await BountyRepository.create(session, parsed_data)
                pass
        except Exception as exc:
            logger.exception("Error processing line %r: %s", line, exc)


def start_scheduler() -> None:
    """Initialises and starts the APScheduler."""
    if not scheduler.running:
        scheduler.add_job(
            fetch_and_process_feed,
            trigger="interval",
            seconds=settings.poll_interval_seconds,
            id="sn_poll_job",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("Scheduler started with interval %s seconds", settings.poll_interval_seconds)


def shutdown_scheduler() -> None:
    """Gracefully shuts down the APScheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")


# --------------------------------------------------------------------------- #
# FastAPI Application
# --------------------------------------------------------------------------- #
app = FastAPI(
    title=settings.title,
    version=settings.version,
    debug=settings.debug,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# --------------------------------------------------------------------------- #
# Exception Handlers
# --------------------------------------------------------------------------- #
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return JSON for HTTPException."""
    logger.warning("HTTPException %s: %s", exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch‑all for uncaught exceptions."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


# --------------------------------------------------------------------------- #
# Startup / Shutdown Events
# --------------------------------------------------------------------------- #
@app.on_event("startup")
async def on_startup() -> None:
    """Initialize resources when the application starts."""
    try:
        # Test DB connection
        async with engine.begin() as conn:
            await conn.run_sync(lambda _: None)
        logger.info("Database connection established")
    except Exception as exc:
        logger.("Database connection failed: %s", exc)
        raise

    try:
        start_scheduler()
    except Exception as exc:
        logger.error("Scheduler start failed: %s", exc)
        raise


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Cleanup resources when the application stops."""
    try:
        shutdown_scheduler()
    except Exception as exc:
        logger.error("Scheduler shutdown error: %s", exc)

    try:
        await engine.dispose()
        logger.info("Database engine disposed")
    except Exception as exc:
        logger.error("Engine disposal error: %s", exc)


# --------------------------------------------------------------------------- #
# Router Registration
# --------------------------------------------------------------------------- #
def include_routers(app: FastAPI) -> None:
    """
    Import and include all API routers.
    This function isolates import errors so the app can still start
    even if a router module has issues.
    """
    try:
        from .routers import api_router  # type: ignore
        app.include_router(api_router)
        logger.info("API router registered")
    except Exception as exc:
        logger.exception("Failed to register API router: %s", exc)
        raise


include_routers(app)

# --------------------------------------------------------------------------- #
# Health‑check endpoint (useful for orchestration)
# --------------------------------------------------------------------------- #
@app.get("/healthz", tags=["health"])
async def health_check() -> dict[str, Any]:
    """Simple health‑check used by Kubernetes / Docker health probes."""
    return {"status": "ok", "version": settings.version}