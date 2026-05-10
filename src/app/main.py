python
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
from typing import Any, AsyncGenerator, Coroutine, Iterable, List, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseSettings, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """Application configuration loaded from environment variables or a .env file."""

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
    http_max_connections: int = Field(10, env="HTTP_MAX_CONNECTIONS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings: Settings = Settings()

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
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
)
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session.

    Yields:
        AsyncSession: An async SQLAlchemy session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# --------------------------------------------------------------------------- #
# Scheduler & Background Job
# --------------------------------------------------------------------------- #
scheduler: AsyncIOScheduler = AsyncIOScheduler()


async def fetch_and_process_feed() -> None:
    """
    Fetches the SN CSV feed, parses rows, and stores new ``OPEN_BOUNTY`` entries.

    The function logs all errors but never raises them to the scheduler,
    ensuring the background job continues running.
    """
    logger.info("Polling SN feed from %s", settings.sn_feed_url)

    limits = httpx.Limits(
        max_keepalive_connections=settings.http_max_connections,
        max_connections=settings.http_max_connections,
    )
    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        verify=True,
        limits=limits,
        http2=True,
    ) as client:
        try:
            response: httpx.Response = await client.get(settings.sn_feed_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch SN feed: %s", exc, exc_info=True)
            return

    # Defensive: ensure we received text data and limit size to 5 MiB.
    if not response.headers.get("content-type", "").startswith("text/"):
        logger.warning("Unexpected content‑type for SN feed: %s", response.headers.get("content-type"))
        return
    if len(response.content) > 5 * 1024 * 1024:
        logger.warning("SN feed payload exceeds safe size limit")
        return

    # Process CSV lines efficiently using a generator.
    def _line_generator(text: str) -> Iterable[str]:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                yield stripped

    for line in _line_generator(response.text):
        try:
            fields: List[str] = line.split("\t")
            # Expected format (example):
            # id, site, type, score, bounty, ...
            bounty_type: str = fields[2].strip()
            if bounty_type != "OPEN_BOUNTY":
                continue

            # Placeholder: replace with actual repository call.
            async with AsyncSessionLocal() as session:
                # await BountyRepository.create(session, parsed_data)
                pass
        except Exception as exc:
            logger.exception("Error processing line %r: %s", line, exc)


def start_scheduler() -> None:
    """Initialises and starts the APScheduler if it is not already running."""
    if scheduler.running:
        logger.debug("Scheduler already running")
        return

    scheduler.add_job(
        fetch_and_process_feed,
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        id="sn_poll_job",
        replace_existing=True,
        misfire_grace_time=30,
    )
    scheduler.start()
    logger.info("Scheduler started with interval %s seconds", settings.poll_interval_seconds)


def shutdown_scheduler() -> None:
    """Gracefully shuts down the APScheduler."""
    if not scheduler.running:
        logger.debug("Scheduler already stopped")
        return

    scheduler.shutdown(wait=False)
    logger.info("Scheduler shut down")


# --------------------------------------------------------------------------- #
# FastAPI Application
# --------------------------------------------------------------------------- #
app: FastAPI = FastAPI(
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
    """
    Return a JSON response for ``HTTPException`` instances.

    Args:
        request: The incoming request (unused, required by FastAPI).
        exc: The raised ``HTTPException``.

    Returns:
        JSONResponse: A JSON payload with the error detail.
    """
    logger.warning(
        "HTTPException %s: %s – path=%s",
        exc.status_code,
        exc.detail,
        request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch‑all handler for unexpected exceptions.

    Args:
        request: The incoming request.
        exc: The uncaught exception.

    Returns:
        JSONResponse: A generic 500 error payload.
    """
    logger.exception("Unhandled exception on %s: %s", request.url.path, exc)
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
        async with engine.begin() as conn:
            await conn.run_sync(lambda _: None)  # simple connectivity test
        logger.info("Database connection established")
    except Exception as exc:
        logger.critical("Database connection failed: %s", exc, exc_info=True)
        raise

    try:
        start_scheduler()
    except Exception as exc:
        logger.error("Scheduler start failed: %s", exc, exc_info=True)
        raise


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Cleanup resources when the application stops."""
    try:
        shutdown_scheduler()
    except Exception as exc:
        logger.error("Scheduler shutdown error: %s", exc, exc_info=True)

    try:
        await engine.dispose()
        logger.info("Database engine disposed")
    except Exception as exc:
        logger.error("Engine disposal error: %s", exc, exc_info=True)


# --------------------------------------------------------------------------- #
# Router Registration
# --------------------------------------------------------------------------- #
def include_routers(app: FastAPI) -> None:
    """
    Import and include all API routers.

    The import is isolated so that a failure in a router module does not
    prevent the application from starting; the error is logged and re‑raised.

    Args:
        app: The FastAPI instance to which routers will be attached.
    """
    try:
        from .routers import api_router  # type: ignore
        app.include_router(api_router)
        logger.info("API router registered")
    except Exception as exc:
        logger.exception("Failed to register API router: %s", exc)
        raise


include_routers(app)
