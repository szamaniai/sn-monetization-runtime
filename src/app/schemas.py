# src/app/schemas.py
"""
Pydantic schemas for request and response payloads used by the
StackExchange "SN" Open Bounty microservice.

The module defines:
* ``BountyEntry`` – a single bounty record parsed from the CSV feed.
* ``BountyFilterRequest`` – optional filtering criteria for API consumers.
* ``BountyListResponse`` – a paginated list of ``BountyEntry`` objects.

All models include validation, clear documentation, and a helper
method for parsing the raw CSV line received from the feed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


class BountyEntry(BaseModel):
    """
    Represents a single ``OPEN_BOUNTY`` entry.

    The CSV payload supplied by the feed has the following columns:

    ``id,site,bounty_id,score,views,answers,answer_rate,upvotes,downvotes,
    owner,tags,title``

    Example::

        1482916,math,2,1702,1000,7,24.1,48657,13566,recent@math,
        "OPEN_BOUNTY,HOT,SELF_POST_OPP","Weekend Puzzle: Interesting Numbers"
    """

    id: int = Field(..., description="Unique identifier for the feed row")
    site: str = Field(..., description="StackExchange site name")
    bounty_id: int = Field(..., description="Bounty identifier on the site")
    score: int = Field(..., description="Current score of the question")
    views: int = Field(..., description="Number of views")
    answers: int = Field(..., description="Number of answers")
    answer_rate: float = Field(..., description="Answer rate as a percentage")
    upvotes: int = Field(..., description="Number of up‑votes")
    downvotes: int = Field(..., description="Number of down‑votes")
    owner: str = Field(..., description="User who posted the question")
    tags: List[str] = Field(
        default_factory=list,
        description="Comma‑separated tags (e.g. ``OPEN_BOUNTY,HOT,SELF_POST_OPP``)",
    )
    title: str = Field(..., description="Question title")

    @validator("answer_rate")
    def _validate_answer_rate(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError("answer_rate must be between 0 and 100")
        return v

    @classmethod
    def from_csv(cls, csv_line: str) -> "BountyEntry":
        """
        Parse a CSV line from the feed into a ``BountyEntry`` instance.

        The function is tolerant to surrounding whitespace and quoted fields.
        It logs parsing errors and re‑raises a ``ValueError`` for callers to handle.
        """
        import csv
        import io

        logger.debug("Parsing CSV line: %s", csv_line)
        try:
            # ``csv.reader`` handles quoted commas and whitespace correctly.
            reader = csv.reader(io.StringIO(csv_line))
            row = next(reader)
        except Exception as exc:
            logger.error("Failed to split CSV line: %s", exc)
            raise ValueError(f"Invalid CSV line: {csv_line}") from exc

        if len(row) != 11:
            logger.error(
                "Unexpected number of columns (%d) in CSV line: %s", len(row), csv_line
            )
            raise ValueError(
                f"Expected 11 columns, got {len(row)}: {csv_line}"
            )

        try:
            (
                id_str,
                site,
                bounty_id_str,
                score_str,
                views_str,
                answers_str,
                answer_rate_str,
                upvotes_str,
                downvotes_str,
                owner,
                tags_str,
                title,
            ) = row
        except ValueError as exc:
            logger.exception("CSV unpacking error")
            raise ValueError("CSV line does not match expected format") from exc

        # Convert numeric fields.
        try:
            id_ = int(id_str)
            bounty_id = int(bounty_id_str)
            score = int(score_str)
            views = int(views_str)
            answers = int(answers_str)
            answer_rate = float(answer_rate_str)
            upvotes = int(upvotes_str)
            downvotes = int(downvotes_str)
        except Exception as exc:
            logger.error("Numeric conversion failed for CSV line: %s", csv_line)
            raise ValueError("Numeric conversion error in CSV line") from exc

        # Tags are stored as a comma‑separated string; split and strip whitespace.
        tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()]

        return cls(
            id=id_,
            site=site.strip(),
            bounty_id=bounty_id,
            score=score,
            views=views,
            answers=answers,
            answer_rate=answer_rate,
            upvotes=upvotes,
            downvotes=downvotes,
            owner=owner.strip(),
            tags=tags,
            title=title.strip(),
        )


class BountyFilterRequest(BaseModel):
    """
    Optional filtering criteria supplied by API consumers.

    All fields are optional – omitted fields are ignored by the service layer.
    """

    site: Optional[str] = Field(
        default=None,
        description="Filter by StackExchange site (e.g. ``math``)",
    )
    min_score: Optional[int] = Field(
        default=None,
        ge=0,
        description="Minimum question score",
    )
    max_score: Optional[int] = Field(
        default=None,
        ge=0,
        description="Maximum question score",
    )
    tags: Optional[List[str]] = Field(
        default=None,
        description="List of tags that must be present (any match is sufficient)",
    )
    is_open: Optional[bool] = Field(
        default=True,
        description="When ``True`` only ``OPEN_BOUNTY`` entries are returned",
    )
    created_after: Optional[datetime] = Field(
        default=None,
        description="Earliest creation datetime (ISO‑8601)",
    )
    created_before: Optional[datetime] = Field(
        default=None,
        description="Latest creation datetime (ISO‑8601)",
    )

    @validator("max_score")
    def _check_score_range(cls, v: Optional[int], values) -> Optional[int]:
        min_score = values.get("min_score")
        if v is not None and min_score is not None and v < min_score:
            raise ValueError("max_score cannot be less than min_score")
        return v


class BountyListResponse(BaseModel):
    """
    Response model for a paginated list of bounty entries.
    """

    total: int = Field(..., description="Total number of matching records")
    items: List[BountyEntry] = Field(..., description="Current page of bounty entries")
    page: int = Field(..., ge=1, description="Current page number (1‑based)")
    page_size: int = Field(..., ge=1, description="Number of items per page")