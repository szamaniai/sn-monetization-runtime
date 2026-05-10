# app/services/bounty_service.py
"""
Business logic for handling StackExchange Open Bounty events.

Features
--------
* Upsert bounty records (insert or update) in an async relational DB.
* Expire bounties whose deadline has passed.
* Manage tags (add, remove, list) on a bounty.
* Dispatch real‑time notifications via Redis Pub/Sub.
* Background task for periodic expiry handling (APScheduler).

The module is deliberately self‑contained – it defines the SQLAlchemy model,
Pydantic schemas, repository helpers and the service class used by the FastAPI
router.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Iterable, List, Optional

import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import HTTPException, status
from pydantic import BaseModel, Field, validator
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    and_,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# --------------------------------------------------------------------------- #
# Configuration – read from environment (fallback defaults for local dev)
# --------------------------------------------------------------------------- #
from dotenv import load_dotenv

load_dotenv()

import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./bounties.db",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
NOTIFY_CHANNEL = os.getenv("NOTIFY_CHANNEL", "bounty_notifications")
EXPIRY_CHECK_INTERVAL_SECONDS = int(os.getenv("EXPIRY_CHECK_INTERVAL", "300"))

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# SQLAlchemy setup
# --------------------------------------------------------------------------- #
Base = declarative_base()


class Bounty(Base):
    """SQLAlchemy model representing a StackExchange bounty."""

    __tablename__ = "bounties"

    id = Column(Integer, primary_key=True, index=True)  # StackExchange bounty ID
    site = Column(String(64), nullable=False, index=True)
    post_id = Column(Integer, nullable=False, index=True)
    reputation = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    user_name = Column(String(128), nullable=False)
    title = Column(Text, nullable=False)
    tags = Column(String(256), nullable=True)  # comma‑separated
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(32), nullable=False, default="OPEN")  # OPEN / EXPIRED
    raw = Column(Text, nullable=True)  # original CSV line for audit


# Async engine / session factory
engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# --------------------------------------------------------------------------- #
# Pydantic schemas
# --------------------------------------------------------------------------- #
class BountyCreate(BaseModel):
    """Schema for incoming bounty data (CSV‑derived)."""

    id: int = Field(..., description="StackExchange bounty identifier")
    site: str = Field(..., description="Site short name, e.g. 'math'")
    post_id: int = Field(..., description="Associated question/answer ID")
    reputation: int = Field(..., description="Bounty amount")
    user_id: int = Field(..., description="User posting the bounty")
    user_name: str = Field(..., description="Display name of the user")
    title: str = Field(..., description="Title of the post")
    tags: List[str] = Field(default_factory=list, description="Tag list")
    created_at: datetime = Field(..., description="When the bounty was created")
    expires_at: datetime = Field(..., description="When the bounty expires")
    raw: Optional[str] = Field(
        None, description="Original CSV line for traceability"
    )

    @validator("created_at", "expires_at", pre=True)
    def parse_dt(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
        return v


class BountyRead(BountyCreate):
    """Schema returned by the API."""

    status: str = Field(..., description="Current status (OPEN / EXPIRED)")


# --------------------------------------------------------------------------- #
# Repository helpers
# --------------------------------------------------------------------------- #
async def get_session() -> AsyncSession:
    """Yield a new async DB session."""
    async with AsyncSessionLocal() as session:
        yield session


# --------------------------------------------------------------------------- #
# Service implementation
# --------------------------------------------------------------------------- #
class BountyService:
    """Encapsulates all business operations for bounty objects."""

    def __init__(
        self,
        redis_url: str = REDIS_URL,
        notify_channel: str = NOTIFY_CHANNEL,
    ) -> None:
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.notify_channel = notify_channel
        self.scheduler = AsyncIOScheduler()
        self._schedule_expiry_job()

    # ------------------------------------------------------------------- #
    # Public API
    # ------------------------------------------------------------------- #
    async def upsert(self, payload: BountyCreate) -> BountyRead:
        """
        Insert a new bounty or update an existing one.

        Parameters
        ----------
        payload: BountyCreate
            Normalised bounty data.

        Returns
        -------
        BountyRead
            The persisted bounty record.
        """
        async for session in get_session():
            try:
                stmt = select(Bounty).where(Bounty.id == payload.id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    logger.debug("Updating existing bounty %s", payload.id)
                    for field, value in payload.model_dump().items():
                        if field == "id":
                            continue
                        setattr(existing, field, value)
                    await session.flush()
                    bounty_obj = existing
                else:
                    logger.info("Creating new bounty %s", payload.id)
                    bounty_obj = Bounty(
                        id=payload.id,
                        site=payload.site,
                        post_id=payload.post_id,
                        reputation=payload.reputation,
                        user_id=payload.user_id,
                        user_name=payload.user_name,
                        title=payload.title,
                        tags=",".join(payload.tags) if payload.tags else None,
                        created_at=payload.created_at,
                        expires_at=payload.expires_at,
                        status="OPEN",
                        raw=payload.raw,
                    )
                    session.add(bounty_obj)

                await session.commit()
                await self._dispatch_notification(bounty_obj)
                return BountyRead(
                    id=bounty_obj.id,
                    site=bounty_obj.site,
                    post_id=bounty_obj.post_id,
                    reputation=bounty_obj.reputation,
                    user_id=bounty_obj.user_id,
                    user_name=bounty_obj.user_name,
                    title=bounty_obj.title,
                    tags=bounty_obj.tags.split(",") if bounty_obj.tags else [],
                    created_at=bounty_obj.created_at,
                    expires_at=bounty_obj.expires_at,
                    status=bounty_obj.status,
                    raw=bounty_obj.raw,
                )
            except SQLAlchemyError as exc:
                logger.exception("Database error during upsert")
                await session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=str(exc),
                ) from exc

    async def expire_bounties(self) -> int:
        """
        Mark all bounties whose ``expires_at`` is in the past as EXPIRED.

        Returns
        -------
        int
            Number of bounties updated.
        """
        now = datetime.now(timezone.utc)
        async for session in get_session():
            try:
                stmt = (
                    update(Bounty)
                    .where(
                        and_(
                            Bounty.expires_at < now,
                            Bounty.status != "EXPIRED",
                        )
                    )
                    .values(status="EXPIRED")
                )
                result = await session.execute(stmt)
                await session.commit()
                updated = result.rowcount or 0
                if updated:
                    logger.info("Expired %d bounty(s)", updated)
                return updated
            except SQLAlchemyError as exc:
                logger.exception("Failed to expire bounties")
                await session.rollback()
                raise

    async def add_tags(self, bounty_id: int, tags: Iterable[str]) -> BountyRead:
        """
        Append tags to a bounty (duplicates are ignored).

        Parameters
        ----------
        bounty_id: int
            Identifier of the bounty.
        tags: Iterable[str]
            New tags to add.

        Returns
        -------
        BountyRead
            Updated bounty.
        """
        async for session in get_session():
            try:
                stmt = select(Bounty).where(Bounty.id == bounty_id)
                result = await session.execute(stmt)
                bounty = result.scalar_one_or_none()
                if not bounty:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Bounty {bounty_id} not found",
                    )

                existing = set(bounty.tags.split(",")) if bounty.tags else set()
                new_tags = set(tags) - existing
                if new_tags:
                    combined = existing.union(new_tags)
                    bounty.tags = ",".join(sorted(combined))
                    await session.flush()
                    await self._dispatch_notification(bounty)

                return BountyRead(
                    id=bounty.id,
                    site=bounty.site,
                    post_id=bounty.post_id,
                    reputation=bounty.reputation,
                    user_id=bounty.user_id,
                    user_name=bounty.user_name,
                    title=bounty.title,
                    tags=bounty.tags.split(",") if bounty.tags else [],
                    created_at=bounty.created_at,
                    expires_at=bounty.expires_at,
                    status=bounty.status,
                    raw=bounty.raw,
                )
            except SQLAlchemyError as exc:
                logger.exception("Failed to add tags")
                await session.rollback()
                raise

    async def list_bounties(
        self,
        *,
        site: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[BountyRead]:
        """
        Retrieve a collection of bounties with optional filters.

        Parameters
        ----------
        site: Optional[str]
            Filter by StackExchange site.
        status: Optional[str]
            Filter by bounty status.
        limit: int
            Maximum number of records to return.

        Returns
        -------
        List[BountyRead]
        """
        async for session in get_session():
            stmt = select(Bounty)
            if site:
                stmt = stmt.where(Bounty.site == site)
            if status:
                stmt = stmt.where(Bounty.status == status)
            stmt = stmt.limit(limit).order_by(Bounty.created_at.desc())
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                BountyRead(
                    id=row.id,
                    site=row.site,
                    post_id=row.post_id,
                    reputation=row.reputation,
                    user_id=row.user_id,
                    user_name=row.user_name,
                    title=row.title,
                    tags=row.tags.split(",") if row.tags else [],
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                    status=row.status,
                    raw=row.raw,
                )
                for row in rows
            ]

    # ------------------------------------------------------------------- #
    # Private helpers
    # ------------------------------------------------------------------- #
    async def _dispatch_notification(self, bounty: Bounty) -> None:
        """
        Publish a JSON payload to the Redis notification channel.

        The payload mirrors the ``BountyRead`` schema.
        """
        payload = {
            "id": bounty.id,
            "site": bounty.site,
            "post_id": bounty.post_id,
            "reputation": bounty.reputation,
            "user_id": bounty.user_id,
            "user_name": bounty.user_name,
            "title": bounty.title,
            "tags": bounty.tags.split(",") if bounty.tags else [],
            "created_at": bounty.created_at.isoformat(),
            "expires_at": bounty.expires_at.isoformat(),
            "status": bounty.status,
        }
        try:
            await self.redis.publish(self.notify_channel, json.dumps(payload))
            logger.debug("Dispatched notification for bounty %s", bounty.id)
        except Exception as exc:  # pragma: no cover – defensive
            logger.exception("Failed to publish notification: %s", exc)

    def _schedule_expiry_job(self) -> None:
        """Register the periodic expiry job with APScheduler."""
        self.scheduler.add_job(
            self.expire_bounties,
            "interval",
            seconds=EXPIRY_CHECK_INTERVAL_SECONDS,
            id="bounty_expiry_job",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info(
            "Scheduled bounty expiry job every %d seconds",
            EXPIRY_CHECK_INTERVAL_SECONDS,
        )


# --------------------------------------------------------------------------- #
# FastAPI router (optional – can be imported by the main app)
# --------------------------------------------------------------------------- #
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/bounties", tags=["bounties"])
service = BountyService()


@router.post("/", response_model=BountyRead, status_code=status.HTTP_201_CREATED)
async def create_or_update_bounty(payload: BountyCreate):
    """Create a new bounty or update an existing one."""
    return await service.upsert(payload)


@router.get("/", response_model=List[BountyRead])
async def get_bounties(
    site: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
):
    """List bounties with optional filtering."""
    return await service.list_bounties(site=site, status=status, limit=limit)


@router.patch("/{bounty_id}/tags", response_model=BountyRead)
async def add_bounty_tags(bounty_id: int, tags: List[str]):
    """Append tags to a bounty."""
    return await service.add_tags(bounty_id, tags)


@router.post("/expire", response_model=dict)
async def trigger_expiry():
    """Manually trigger the expiry routine."""
    count = await service.expire_bounties()
    return {"expired": count}