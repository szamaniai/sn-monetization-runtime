python
# app/main.py
"""
FastAPI entry point for the Open Bounty micro‑service.

Features
--------
- Loads environment configuration with validation.
- Initializes async SQLAlchemy engine & session factory.
- Sets up async Redis client.
- Starts APScheduler background job that polls the StackExchange Open Bounty feed.
- Provides health‑check endpoint and manual trigger.
- Implements comprehensive error handling, logging, type hints,
  docstrings, input validation and performance‑oriented patterns.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, AsyncGenerator, Dict, Literal, Optional

import httpx
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BaseSettings, Field, ValidationError, validator
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """Application configuration with validation."""

    api_title: str = Field("Open Bounty Service", env="API_TITLE")
    api_version: str = Field("0.1.0", env="API_VERSION")
    api_description: str = Field(
        "CRUD & notifications for StackExchange Open Bounties", env="API_DESCRIPTION"
    )
    host: str = Field("0.0.0.0", env="HOST")
    port: int = Field(8000, env="PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO", env="LOG_LEVEL"
    )
    database_url: str = Field(
        "sqlite+aiosqlite:///./bounty.db", env="DATABASE_URL"
    )
    redis_url: str = Field("redis://localhost:6379/0", env="REDIS_URL")
    radar_interval: int = Field(300, env="RADAR_INTERVAL")  # seconds

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("open_bounty")

# --------------------------------------------------------------------------- #
# FastAPI application
# --------------------------------------------------------------------------- #
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
    docs_url="/docs",
    redoc_url="/redoc",
)

# --------------------------------------------------------------------------- #
# Database utilities
# --------------------------------------------------------------------------- #
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    future=True,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async SQLAlchemy session.

    Yields
    ------
    AsyncSession
        An active database session.

    Raises
    ------
    HTTPException
        If session creation fails.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception as exc:  # pragma: no cover
            logger.exception("Failed to create DB session")
            raise HTTPException(status_code=500, detail="Database connection error") from exc


# --------------------------------------------------------------------------- #
# Redis client
# --------------------------------------------------------------------------- #
redis_client: Optional[Redis] = None


async def get_redis() -> Redis:
    """
    FastAPI dependency that returns the initialized Redis client.

    Returns
    -------
    Redis
        The Redis client instance.

    Raises
    ------
    RuntimeError
        If the client has not been initialized.
    """
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    return redis_client


# --------------------------------------------------------------------------- #
# Pydantic models for validation
# --------------------------------------------------------------------------- #
class OwnerModel(BaseModel):
    """Validated owner payload."""

    display_name: Optional[str] = Field(default=None, max_length=100)


class BountyModel(BaseModel):
    """Validated bounty payload."""

    bounty_id: int = Field(..., ge=1)
    title: Optional[str] = Field(default=None, max_length=300)
    amount: Optional[int] = Field(default=None, ge=0)
    creation_date: Optional[int] = Field(default=None, ge=0)
    owner: Optional[OwnerModel] = None
    link: Optional[str] = Field(default=None, max_length=500)

    @validator("link")
    def _validate_link(cls, v: Optional[str]) -> Optional[str]:
        """Reject non‑HTTP URLs for security."""
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("Invalid link scheme")
        return v


# --------------------------------------------------------------------------- #
# External API constants
# --------------------------------------------------------------------------- #
BOUNTY_FEED_URL: str = (
    "https://api.stackexchange.com/2.3/bounties?"
    "order=desc&sort=activity&site=math&filter=default"
)

# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
async def _publish_to_redis(payload: BountyModel) -> None:
    """
    Publish a validated bounty payload to Redis.

    Parameters
    ----------
    payload : BountyModel
        Normalised and validated bounty data.

    Raises
    ------
    RuntimeError
        If publishing fails.
    """
    client = await get_redis()
    try:
        await client.publish("bounty_updates", payload.json())
        logger.debug("Published bounty %s to Redis", payload.bounty_id)
    except Exception as exc:  # pragma: no cover
        logger.exception("Redis publish failed for bounty %s", payload.bounty_id)
        raise RuntimeError("Redis publish failed") from exc


def _map_feed_item(item: Dict[str, Any]) -> BountyModel:
    """
    Convert a raw StackExchange feed item into a validated BountyModel.

    Parameters
    ----------
    item : dict
        Raw JSON object from the API.

    Returns
    -------
    BountyModel
        Validated model instance.

    Raises
    ------
    ValidationError
        If the payload does not satisfy the schema.
    """
    owner_data = {"display_name": item.get("owner", {}).get("display_name")}
    bounty_data = {
        "bounty_id": item.get("bounty_id"),
        "title": item.get("title"),
        "amount": item.get("amount"),
        "creation_date": item.get("creation_date"),
        "owner": owner_data,
        "link": item.get("link"),
    }
    return BountyModel(**bounty_data)


# --------------------------------------------------------------------------- #
# Radar job – fetches open bounty feed and pushes to Redis
# --------------------------------------------------------------------------- #
async def fetch_and_publish_bounties() -> None:
    """
    Poll StackExchange API for open bounties and publish each to Redis.

    This coroutine is scheduled by APScheduler and runs in the background.

    Raises
    ------
    RuntimeError
        If the HTTP request fails or response validation fails.
    """
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(BOUNTY_FEED_URL, follow_redirects=True)
            response.raise_for_status()
        except httpx.RequestError as exc:
            logger.error("Network error while fetching bounty feed: %s", exc)
            raise RuntimeError("Network error while fetching bounty feed") from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Unexpected HTTP status %s from bounty feed: %s",
                exc.response.status_code,
                exc,
            )
            raise RuntimeError(
                f"Unexpected HTTP status {exc.response.status_code}"
            ) from exc

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON received from bounty feed: %s", exc)
            raise RuntimeError("Invalid JSON from bounty feed") from exc

        items = data.get("items", [])
        logger.info("Fetched %d bounty items", len(items))

        for item in items:
            try:
                bounty = _map_feed_item(item)
                await _publish_to_redis(bounty)
            except ValidationError as exc:
                logger.warning("Skipping invalid bounty item: %s", exc)
            except RuntimeError as exc:
                logger.error("Failed to publish bounty %s: %s", item.get("bounty_id"), exc)


# --------------------------------------------------------------------------- #
# Scheduler lifecycle
# --------------------------------------------------------------------------- #
scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def on_startup() -> None:
    """Initialize resources and start background scheduler."""
    global redis_client
    try:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info("Connected to Redis at %s", settings.redis_url)
    except Exception as exc:
        logger.exception("Failed to connect to Redis")
        raise RuntimeError("Redis connection failed") from exc

    scheduler.add_job(
        fetch_and_publish_bounties,
        trigger=IntervalTrigger(seconds=settings.radar_interval),
        name="fetch_and_publish_bounties",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler started with interval %d seconds", settings.radar_interval)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Gracefully shutdown scheduler and Redis client."""
    scheduler.shutdown(wait=False)
    if redis_client:
        await redis_client.close()
        logger.info("Redis client closed")
    await engine.dispose()
    logger.info("Database engine disposed")


# --------------------------------------------------------------------------- #
# API endpoints
# --------------------------------------------------------------------------- #
@app.get(
    "/health",
    status_code=status.HTTP_200_OK,
    response_model=dict,
    summary="Health check",
    description="Returns basic health information for the service.",
)
async def health_check() -> dict:
    """Return health status."""
    return {"status": "healthy", "detail": "Service is operational"}


@app.post(
    "/trigger",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Manual trigger",
    description="Manually trigger a bounty fetch and publish cycle.",
)
async def trigger_fetch() -> dict:
    """Manually invoke the background fetch job."""
    try:
        await fetch_and_publish_bounties()
        logger.info("Manual bounty fetch completed")
        return {"status": "accepted", "detail": "Bounty fetch started"}
    except RuntimeError as exc:
        logger.error("Manual trigger failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# --------------------------------------------------------------------------- #
# Global exception handlers
# --------------------------------------------------------------------------- #
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle request validation errors."""
    logger.warning("Validation error for %s: %s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Handle HTTP exceptions."""
    logger.error("HTTP exception for %s: %s", request.url.path, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
