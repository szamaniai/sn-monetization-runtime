python
# app/api/bounty.py
"""
FastAPI router exposing CRUD and query endpoints for StackExchange Open Bounty data.

Features
--------
* Async‑compatible SQLAlchemy ORM model ``Bounty``
* Pydantic schemas for request/response validation
* Dependency for async DB session
* CRUD endpoints with comprehensive error handling, type hints,
  logging, docstrings, input validation and security sanitisation
* Pagination support for list endpoint
"""

from __future__ import annotations

import logging
from typing import List, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, validator
from sqlalchemy import Column, Float, Integer, String, delete, select, update
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

# --------------------------------------------------------------------------- #
# Logger configuration – the application may configure the root logger
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Database configuration – replace with production DB URI (e.g., PostgreSQL)
# --------------------------------------------------------------------------- #
DATABASE_URL = "sqlite+aiosqlite:///./bounty.db"  # placeholder for production

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)

Base = declarative_base()


# --------------------------------------------------------------------------- #
# ORM model
# --------------------------------------------------------------------------- #
class Bounty(Base):
    """SQLAlchemy ORM model representing an Open Bounty entry."""

    __tablename__ = "bounties"

    id: int = Column(Integer, primary_key=True, index=True, autoincrement=True)
    bounty_id: int = Column(Integer, unique=True, nullable=False, index=True)
    site: str = Column(String(64), nullable=False, index=True)
    user_id: int = Column(Integer, nullable=False)
    question_id: int = Column(Integer, nullable=False)
    amount: int = Column(Integer, nullable=False)
    days_left: int = Column(Integer, nullable=False)
    reputation: float = Column(Float, nullable=False)
    view_count: int = Column(Integer, nullable=False)
    answer_count: int = Column(Integer, nullable=False)
    owner: str = Column(String(128), nullable=False)
    tags: str = Column(String(256), nullable=False)  # comma‑separated
    title: str = Column(String(256), nullable=False)

    def __repr__(self) -> str:
        return (
            f"Bounty(id={self.id}, bounty_id={self.bounty_id}, site={self.site!r}, "
            f"title={self.title!r})"
        )


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _sanitize_str(value: str, max_length: int) -> str:
    """
    Trim whitespace and enforce a maximum length.

    Args:
        value: Input string.
        max_length: Maximum allowed length.

    Returns:
        Sanitised string.

    Raises:
        ValueError: If the string exceeds ``max_length``.
    """
    stripped = value.strip()
    if len(stripped) > max_length:
        raise ValueError(f"String exceeds maximum length of {max_length}")
    return stripped


def _model_to_dict(bounty: Bounty) -> dict:
    """
    Convert a ``Bounty`` ORM instance to a dict compatible with Pydantic.

    Args:
        bounty: ORM instance.

    Returns:
        Dictionary representation of the bounty.
    """
    return {
        "id": bounty.id,
        "bounty_id": bounty.bounty_id,
        "site": bounty.site,
        "user_id": bounty.user_id,
        "question_id": bounty.question_id,
        "amount": bounty.amount,
        "days_left": bounty.days_left,
        "reputation": bounty.reputation,
        "view_count": bounty.view_count,
        "answer_count": bounty.answer_count,
        "owner": bounty.owner,
        "tags": [t.strip() for t in bounty.tags.split(",") if t.strip()],
        "title": bounty.title,
    }


# --------------------------------------------------------------------------- #
# Pydantic schemas
# --------------------------------------------------------------------------- #
class BountyBase(BaseModel):
    """Base fields shared by read and write schemas."""

    bounty_id: int = Field(..., description="Unique bounty identifier")
    site: str = Field(..., max_length=64, description="StackExchange site")
    user_id: int = Field(..., description="User offering the bounty")
    question_id: int = Field(..., description="Associated question identifier")
    amount: int = Field(..., description="Bounty amount (reputation points)")
    days_left: int = Field(..., description="Days remaining for the bounty")
    reputation: float = Field(..., description="Owner reputation at posting")
    view_count: int = Field(..., description="Number of question views")
    answer_count: int = Field(..., description="Number of answers")
    owner: str = Field(..., max_length=128, description="Owner display name")
    tags: List[str] = Field(
        default_factory=list,
        description="List of tags associated with the question",
    )
    title: str = Field(..., max_length=256, description="Question title")

    @validator("site", "owner", "title", pre=True)
    def _strip_and_limit(cls, v: str, field) -> str:  # type: ignore[override]
        return _sanitize_str(v, field.field_info.max_length)  # type: ignore[arg-type]

    @validator("tags", pre=True)
    def _parse_tags(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v


class BountyCreate(BountyBase):
    """Schema for creating a new bounty."""


class BountyRead(BountyBase):
    """Schema for reading bounty data."""

    id: int

    class Config:
        orm_mode = True


class BountyUpdate(BaseModel):
    """Schema for partial bounty updates."""

    site: str | None = None
    user_id: int | None = None
    question_id: int | None = None
    amount: int | None = None
    days_left: int | None = None
    reputation: float | None = None
    view_count: int | None = None
    answer_count: int | None = None
    owner: str | None = None
    tags: List[str] | None = None
    title: str | None = None

    @validator("site", "owner", "title", pre=True)
    def _strip_and_limit(cls, v, field):
        if v is None:
            return v
        return _sanitize_str(v, field.field_info.max_length)  # type: ignore[arg-type]

    @validator("tags", pre=True)
    def _parse_tags(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v


# --------------------------------------------------------------------------- #
# Dependency – async DB session
# --------------------------------------------------------------------------- #
async def get_db() -> AsyncSession:
    """
    Provide an async SQLAlchemy session for request handling.

    Yields:
        AsyncSession: The database session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/bounties", tags=["bounties"])


# --------------------------------------------------------------------------- #
# CRUD Endpoints
# --------------------------------------------------------------------------- #
@router.post(
    "/",
    response_model=BountyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new bounty",
)
async def create_bounty(
    payload: BountyCreate,
    db: AsyncSession = Depends(get_db),
) -> BountyRead:
    """
    Create a new bounty entry.

    Args:
        payload: Bounty data to persist.
        db: Async SQLAlchemy session.

    Returns:
        The created bounty.

    Raises:
        HTTPException: If the bounty already exists or validation fails.
    """
    logger.debug("Attempting to create bounty %s", payload.bounty_id)
    stmt = select(Bounty).where(Bounty.bounty_id == payload.bounty_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing:
        logger.warning("Bounty %s already exists", payload.bounty_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Bounty with id {payload.bounty_id} already exists",
        )
    bounty = Bounty(
        bounty_id=payload.bounty_id,
        site=payload.site,
        user_id=payload.user_id,
        question_id=payload.question_id,
        amount=payload.amount,
        days_left=payload.days_left,
        reputation=payload.reputation,
        view_count=payload.view_count,
        answer_count=payload.answer_count,
        owner=payload.owner,
        tags=",".join(payload.tags),
        title=payload.title,
    )
    db.add(bounty)
    try:
        await db.commit()
        await db.refresh(bounty)
    except IntegrityError as exc:
        await db.rollback()
        logger.error("Integrity error while creating bounty: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integrity error while creating bounty",
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Database error while creating bounty")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while creating bounty",
        ) from exc
    logger.info("Created bounty %s (id=%s)", bounty.bounty_id, bounty.id)
    return BountyRead(**_model_to_dict(bounty))


@router.get(
    "/",
    response_model=List[BountyRead],
    summary="List bounties with pagination",
)
async def list_bounties(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    db: AsyncSession = Depends(get_db),
) -> List[BountyRead]:
    """
    Retrieve a paginated list of bounties.

    Args:
        skip: Number of records to skip.
        limit: Maximum number of records to return.
        db: Async SQLAlchemy session.

    Returns:
        List of bounties.

    Raises:
        HTTPException: If a database error occurs.
    """
    logger.debug("Listing bounties: skip=%s, limit=%s", skip, limit)
    stmt = select(Bounty).offset(skip).limit(limit)
    try:
        result = await db.execute(stmt)
    except SQLAlchemyError as exc:
        logger.exception("Database error while listing bounties")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while listing bounties",
        ) from exc
    bounties = result.scalars().all()
    logger.info("Returned %s bounties", len(bounties))
    return [BountyRead(**_model_to_dict(b)) for b in bounties]


@router.get(
    "/{bounty_id}",
    response_model=BountyRead,
    summary="Retrieve a single bounty by its bounty_id",
)
async def get_bounty(
    bounty_id: int,
    db: AsyncSession = Depends(get_db),
) -> BountyRead:
    """
    Fetch a single bounty entry.

    Args:
        bounty_id: Unique identifier of the bounty.
        db: Async SQLAlchemy session.

    Returns:
        The requested bounty.

    Raises:
        HTTPException: If the bounty does not exist or a DB error occurs.
    """
    logger.debug("Fetching bounty %s", bounty_id)
    stmt = select(Bounty).where(Bounty.bounty_id == bounty_id)
    try:
        result = await db.execute(stmt)
    except SQLAlchemyError as exc:
        logger.exception("Database error while fetching bounty %s", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while fetching bounty",
        ) from exc
    bounty = result.scalar_one_or_none()
    if not bounty:
        logger.warning("Bounty %s not found", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bounty with id {bounty_id} not found",
        )
    logger.info("Fetched bounty %s (id=%s)", bounty.bounty_id, bounty.id)
    return BountyRead(**_model_to_dict(bounty))


@router.patch(
    "/{bounty_id}",
    response_model=BountyRead,
    summary="Partially update a bounty",
)
async def update_bounty(
    bounty_id: int,
    payload: BountyUpdate,
    db: AsyncSession = Depends(get_db),
) -> BountyRead:
    """
    Partially update a bounty entry.

    Args:
        bounty_id: Identifier of the bounty to update.
        payload: Fields to update.
        db: Async SQLAlchemy session.

    Returns:
        Updated bounty.

    Raises:
        HTTPException: If the bounty does not exist or a DB error occurs.
    """
    logger.debug("Updating bounty %s with %s", bounty_id, payload.dict(exclude_unset=True))
    stmt = select(Bounty).where(Bounty.bounty_id == bounty_id)
    try:
        result = await db.execute(stmt)
    except SQLAlchemyError as exc:
        logger.exception("Database error while locating bounty %s for update", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while locating bounty",
        ) from exc
    bounty = result.scalar_one_or_none()
    if not bounty:
        logger.warning("Bounty %s not found for update", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bounty with id {bounty_id} not found",
        )
    update_data = payload.dict(exclude_unset=True)
    for field, value in update_data.items():
        if field == "tags" and isinstance(value, list):
            setattr(bounty, "tags", ",".join(value))
        else:
            setattr(bounty, field, value)
    try:
        await db.commit()
        await db.refresh(bounty)
    except IntegrityError as exc:
        await db.rollback()
        logger.error("Integrity error while updating bounty %s: %s", bounty_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integrity error while updating bounty",
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Database error while updating bounty %s", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while updating bounty",
        ) from exc
    logger.info("Updated bounty %s (id=%s)", bounty.bounty_id, bounty.id)
    return BountyRead(**_model_to_dict(bounty))


@router.delete(
    "/{bounty_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a bounty",
)
async def delete_bounty(
    bounty_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a bounty entry.

    Args:
        bounty_id: Identifier of the bounty to delete.
        db: Async SQLAlchemy session.

    Raises:
        HTTPException: If the bounty does not exist or a DB error occurs.
    """
    logger.debug("Deleting bounty %s", bounty_id)
    stmt = select(Bounty).where(Bounty.bounty_id == bounty_id)
    try:
        result = await db.execute(stmt)
    except SQLAlchemyError as exc:
        logger.exception("Database error while locating bounty %s for deletion", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while locating bounty",
        ) from exc
    bounty = result.scalar_one_or_none()
    if not bounty:
        logger.warning("Bounty %s not found for deletion", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bounty with id {bounty_id} not found",
        )
    del_stmt = delete(Bounty).where(Bounty.id == bounty.id)
    try:
        await db.execute(del_stmt)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Database error while deleting bounty %s", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while deleting bounty",
        ) from exc
    logger.info("Deleted bounty %s (id=%s)", bounty.bounty_id, bounty.id)
