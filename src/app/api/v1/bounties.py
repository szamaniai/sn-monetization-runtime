python
# src/app/api/v1/bounties.py
"""FastAPI router for bounty endpoints.

Provides three public routes:
* ``GET /bounties`` – list bounties with optional pagination.
* ``GET /bounties/{bounty_id}`` – retrieve a single bounty by its primary key.
* ``GET /bounties/search`` – free‑text search on title, tags or owner.

All routes delegate to the service layer and return Pydantic models.
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status

# Service layer – imported from the application package.
# The concrete implementation lives in ``src/app/services/bounty_service.py``.
from app.services.bounty_service import BountyService, get_bounty_service

# Pydantic response schemas – defined in ``src/app/schemas/bounty.py``.
from app.schemas.bounty import BountyOut

router = APIRouter(
    prefix="/bounties",
    tags=["bounties"],
    responses={status.HTTP_404_NOT_FOUND: {"description": "Bounty not found"}},
)

log = logging.getLogger(__name__)


@router.get(
    "/",
    response_model=List[BountyOut],
    summary="List bounties",
    description="Return a paginated list of all stored bounties.",
)
async def list_bounties(
    *,
    service: BountyService = Depends(get_bounty_service),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of records to return"),
) -> List[BountyOut]:
    """
    Retrieve a slice of bounties.

    Args:
        service: Business‑logic service injected by FastAPI.
        skip: Number of rows to skip (for pagination).
        limit: Maximum number of rows to return.

    Returns:
        A list of :class:`BountyOut` objects.

    Raises:
        HTTPException: If an unexpected error occurs while fetching data.
    """
    log.debug("Listing bounties – skip=%s, limit=%s", skip, limit)
    try:
        bounties = await service.list_bounties(skip=skip, limit=limit)
    except Exception as exc:  # pragma: no cover
        log.exception("Failed to list bounties")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve bounties at this time.",
        ) from exc
    return bounties


@router.get(
    "/{bounty_id}",
    response_model=BountyOut,
    summary="Get a bounty by ID",
    description="Return a single bounty identified by its primary key.",
)
async def get_bounty(
    bounty_id: int,
    service: BountyService = Depends(get_bounty_service),
) -> BountyOut:
    """
    Retrieve a single bounty.

    Args:
        bounty_id: Primary‑key of the bounty.
        service: Business‑logic service injected by FastAPI.

    Raises:
        HTTPException: If the bounty does not exist or an internal error occurs.

    Returns:
        A :class:`BountyOut` instance.
    """
    log.debug("Fetching bounty with id=%s", bounty_id)
    try:
        bounty = await service.get_bounty_by_id(bounty_id)
    except Exception as exc:  # pragma: no cover
        log.exception("Error while fetching bounty id=%s", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve the bounty at this time.",
        ) from exc

    if bounty is None:
        log.warning("Bounty not found – id=%s", bounty_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bounty with id {bounty_id} not found",
        )
    return bounty


@router.get(
    "/search",
    response_model=List[BountyOut],
    summary="Search bounties",
    description="Free‑text search on title, tags or owner. Supports pagination.",
)
async def search_bounties(
    *,
    query: str = Query(..., min_length=1, description="Search term"),
    service: BountyService = Depends(get_bounty_service),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of records to return"),
) -> List[BountyOut]:
    """
    Search bounties using a simple case‑insensitive substring match.

    Args:
        query: Text to search for. Whitespace is stripped before processing.
        service: Business‑logic service injected by FastAPI.
        skip: Pagination offset.
        limit: Pagination size.

    Returns:
        A list of matching :class:`BountyOut` objects.

    Raises:
        HTTPException: If an unexpected error occurs during the search.
    """
    sanitized_query = query.strip()
    if not sanitized_query:
        log.info("Empty search query received; returning empty result set")
        return []

    log.debug(
        "Searching bounties – query=%r, skip=%s, limit=%s",
        sanitized_query,
        skip,
        limit,
    )
    try:
        results = await service.search_bounties(query=sanitized_query, skip=skip, limit=limit)
    except Exception as exc:  # pragma: no cover
        log.exception("Search failed for query=%r", sanitized_query)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to perform search at this time.",
        ) from exc

    if not results:
        log.info("No bounties matched query=%r", sanitized_query)
    return results
