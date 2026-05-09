# src/app/repositories.py
"""Data‑access layer for Bounty entities.

Provides a thin, type‑safe wrapper around SQLAlchemy async sessions.
All CRUD operations are implemented with proper error handling,
logging and type hints.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Bounty  # SQLAlchemy model (defined elsewhere)
from .schemas import BountyCreate, BountyUpdate  # Pydantic schemas (defined elsewhere)

logger = logging.getLogger(__name__)


class NotFoundError(RuntimeError):
    """Raised when a requested Bounty cannot be found in the database."""


class BountyRepository:
    """Repository encapsulating all DB interactions for the ``Bounty`` model."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Args:
            session: An ``AsyncSession`` instance injected by the service layer.
        """
        self._session = session

    # --------------------------------------------------------------------- #
    # READ
    # --------------------------------------------------------------------- #
    async def get(self, bounty_id: int) -> Optional[Bounty]:
        """Return a single ``Bounty`` by its primary key.

        Args:
            bounty_id: Primary key of the bounty.

        Returns:
            The ``Bounty`` instance if found, otherwise ``None``.
        """
        stmt = select(Bounty).where(Bounty.id == bounty_id)
        try:
            result = await self._session.execute(stmt)
            bounty = result.scalar_one_or_none()
            logger.debug("Fetched bounty %s: %s", bounty_id, bounty)
            return bounty
        except SQLAlchemyError as exc:
            logger.exception("Database error while fetching bounty %s", bounty_id)
            raise

    async def list(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Bounty]:
        """Return a paginated list of bounties optionally filtered.

        Args:
            skip: Number of rows to skip (offset).
            limit: Maximum number of rows to return.
            filters: Mapping of column names to values for exact matching.

        Returns:
            List of ``Bounty`` objects.
        """
        stmt = select(Bounty).offset(skip).limit(limit)

        if filters:
            for column_name, value in filters.items():
                column = getattr(Bounty, column_name, None)
                if column is None:
                    logger.warning("Invalid filter column %s ignored", column_name)
                    continue
                stmt = stmt.where(column == value)

        try:
            result = await self._session.execute(stmt)
            bounties = result.scalars().all()
            logger.debug(
                "Listed bounties (skip=%s, limit=%s, filters=%s): %s",
                skip,
                limit,
                filters,
                len(bounties),
            )
            return bounties
        except SQLAlchemyError as exc:
            logger.exception("Database error while listing bounties")
            raise

    # --------------------------------------------------------------------- #
    # CREATE
    # --------------------------------------------------------------------- #
    async def create(self, payload: BountyCreate) -> Bounty:
        """Insert a new bounty record.

        Args:
            payload: Pydantic model containing the data to store.

        Returns:
            The newly created ``Bounty`` instance with its primary key populated.
        """
        bounty = Bounty(**payload.model_dump())
        self._session.add(bounty)
        try:
            await self._session.flush()  # Populate PK without committing
            await self._session.commit()
            logger.info("Created bounty %s", bounty.id)
            return bounty
        except SQLAlchemyError as exc:
            await self._session.rollback()
            logger.exception("Failed to create bounty")
            raise

    # --------------------------------------------------------------------- #
    # UPDATE
    # --------------------------------------------------------------------- #
    async def update(self, bounty_id: int, payload: BountyUpdate) -> Bounty:
        """Update an existing bounty.

        Args:
            bounty_id: Primary key of the bounty to update.
            payload: Pydantic model with fields to modify.

        Returns:
            The updated ``Bounty`` instance.

        Raises:
            NotFoundError: If the bounty does not exist.
        """
        stmt = (
            update(Bounty)
            .where(Bounty.id == bounty_id)
            .values(**payload.model_dump(exclude_unset=True))
            .execution_options(synchronize_session="fetch")
        )
        try:
            result = await self._session.execute(stmt)
            if result.rowcount == 0:
                raise NotFoundError(f"Bounty {bounty_id} not found")
            await self._session.commit()
            bounty = await self.get(bounty_id)
            logger.info("Updated bounty %s", bounty_id)
            return bounty  # type: ignore[return-value]
        except SQLAlchemyError as exc:
            await self._session.rollback()
            logger.exception("Failed to update bounty %s", bounty_id)
            raise

    # --------------------------------------------------------------------- #
    # DELETE
    # --------------------------------------------------------------------- #
    async def delete(self, bounty_id: int) -> None:
        """Delete a bounty by its primary key.

        Args:
            bounty_id: Primary key of the bounty to delete.

        Raises:
            NotFoundError: If the bounty does not exist.
        """
        stmt = delete(Bounty).where(Bounty.id == bounty_id)
        try:
            result = await self._session.execute(stmt)
            if result.rowcount == 0:
                raise NotFoundError(f"Bounty {bounty_id} not found")
            await self._session.commit()
            logger.info("Deleted bounty %s", bounty_id)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            logger.exception("Failed to delete bounty %s", bounty_id)
            raise