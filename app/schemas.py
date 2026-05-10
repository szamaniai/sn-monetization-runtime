python
# app/schemas.py
"""
Pydantic models for the Open Bounty micro‑service.

The module defines:
* ``BountyBase`` – shared attributes for creation, update and response.
* ``BountyCreate`` – payload for creating a new bounty.
* ``BountyUpdate`` – payload for partial updates (all fields optional).
* ``BountyResponse`` – representation returned by the API (includes DB identifier).

All models enforce type safety, perform sanitisation, and are configured for
ORM compatibility with SQLAlchemy. Comprehensive logging, type hints,
docstrings and error handling are provided to meet production‑grade
standards.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, validator

# --------------------------------------------------------------------------- #
# Logging configuration – the module uses the application logger.
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants – limits and patterns used for validation / sanitisation.
# --------------------------------------------------------------------------- #
_MAX_TITLE_LENGTH: int = 256
_MAX_OWNER_LENGTH: int = 128
_TAG_PATTERN: re.Pattern = re.compile(r"^[\w-]+$")  # alphanum, underscore, hyphen
_SITE_PATTERN: re.Pattern = re.compile(r"^[a-z0-9]+$")  # simple site identifier


def _sanitize_str(value: str, field_name: str, max_len: int) -> str:
    """
    Strip whitespace, enforce length and log the operation.

    Parameters
    ----------
    value : str
        Raw input string.
    field_name : str
        Human‑readable name for logging.
    max_len : int
        Maximum allowed length.

    Returns
    -------
    str
        Sanitised string.

    Raises
    ------
    TypeError
        If ``value`` is not a string.
    ValueError
        If the string exceeds ``max_len`` after stripping.
    """
    if not isinstance(value, str):
        logger.error("%s must be a string, got %s", field_name, type(value).__name__)
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if len(cleaned) > max_len:
        logger.error(
            "%s exceeds maximum length (%d > %d)", field_name, len(cleaned), max_len
        )
        raise ValueError(f"{field_name} must be ≤ {max_len} characters")
    logger.debug("%s sanitised to: %s", field_name, cleaned)
    return cleaned


def _parse_tags_raw(raw: Union[str, List[object]]) -> List[str]:
    """
    Normalise tags from a comma‑separated string or a list.

    Parameters
    ----------
    raw : Union[str, List[object]]
        Raw tags input.

    Returns
    -------
    List[str]
        Cleaned list of tags.

    Raises
    ------
    TypeError
        If ``raw`` is neither a string nor a list.
    ValueError
        If a tag does not match the allowed pattern.
    """
    if isinstance(raw, str):
        candidates = [t.strip() for t in raw.split(",") if t.strip()]
    elif isinstance(raw, list):
        candidates = [str(t).strip() for t in raw if str(t).strip()]
    else:
        logger.error("Invalid tags type: %s", type(raw).__name__)
        raise TypeError("tags must be a string or a list")

    tags: List[str] = []
    for tag in candidates:
        if not _TAG_PATTERN.fullmatch(tag):
            logger.warning("Invalid tag detected: %s", tag)
            raise ValueError(f"Invalid tag: {tag}")
        tags.append(tag)

    logger.debug("Parsed tags: %s", tags)
    return tags


def _validate_site(site: str) -> str:
    """
    Validate the StackExchange site identifier.

    Parameters
    ----------
    site : str
        Raw site identifier.

    Returns
    -------
    str
        Sanitised site identifier.

    Raises
    ------
    ValueError
        If the site does not match the allowed pattern.
    """
    sanitized = _sanitize_str(site, "site", 64)
    if not _SITE_PATTERN.fullmatch(sanitized):
        logger.error("Site identifier validation failed: %s", sanitized)
        raise ValueError(f"Invalid site identifier: {sanitized}")
    return sanitized


# --------------------------------------------------------------------------- #
# Core data model – shared fields.
# --------------------------------------------------------------------------- #
class BountyBase(BaseModel):
    """
    Core attributes of a bounty entry.

    Attributes
    ----------
    site : str
        StackExchange site identifier (e.g. ``math``).
    reputation : int
        Reputation of the user who offered the bounty.
    question_id : int
        Identifier of the question the bounty is attached to.
    answer_id : Optional[int]
        Identifier of the answer that received the bounty (if any).
    user_id : int
        Identifier of the user who posted the bounty.
    bounty_amount : float
        Monetary amount of the bounty (in the site‑specific unit).
    view_count : int
        Number of times the question has been viewed.
    answer_count : int
        Number of answers posted for the question.
    owner : str
        Username or e‑mail of the bounty owner.
    tags : List[str]
        List of tags associated with the question.
    title : str
        Human‑readable title of the bounty.
    created_at : datetime
        Timestamp when the bounty was first observed.
    """

    site: str = Field(..., description="StackExchange site identifier")
    reputation: int = Field(..., ge=0, description="User reputation")
    question_id: int = Field(..., ge=0, description="Question identifier")
    answer_id: Optional[int] = Field(
        None, ge=0, description="Answer identifier (if awarded)"
    )
    user_id: int = Field(..., ge=0, description="User identifier")
    bounty_amount: float = Field(..., gt=0, description="Bounty amount")
    view_count: int = Field(..., ge=0, description="Question view count")
    answer_count: int = Field(..., ge=0, description="Number of answers")
    owner: str = Field(..., description="Owner username or e‑mail")
    tags: List[str] = Field(..., description="Comma‑separated tags")
    title: str = Field(..., description="Bounty title")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp of bounty creation",
    )

    model_config = ConfigDict(
        orm_mode=True,
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
    )

    @validator("site", pre=True)
    def _validate_site(cls, v: str) -> str:
        """
        Validate and sanitise the ``site`` field.

        Parameters
        ----------
        v : str
            Raw site identifier.

        Returns
        -------
        str
            Sanitised site identifier.

        Raises
        ------
        ValueError
            If validation fails.
        """
        try:
            return _validate_site(v)
        except Exception as exc:
            logger.exception("Site validation failed: %s", exc)
            raise ValueError("Invalid site") from exc

    @validator("tags", pre=True)
    def _validate_tags(cls, v: Union[str, List[object]]) -> List[str]:
        """
        Normalise the ``tags`` field.

        Parameters
        ----------
        v : Union[str, List[object]]
            Raw tags input.

        Returns
        -------
        List[str]
            Cleaned list of tags.

        Raises
        ------
        ValueError
            If tag validation fails.
        """
        try:
            return _parse_tags_raw(v)
        except Exception as exc:
            logger.exception("Tag validation failed: %s", exc)
            raise ValueError("Invalid tags") from exc

    @validator("title")
    def _validate_title(cls, v: str) -> str:
        """
        Ensure the title is non‑empty and within length limits.

        Parameters
        ----------
        v : str
            Raw title.

        Returns
        -------
        str
            Sanitised title.

        Raises
        ------
        ValueError
            If the title is empty or too long.
        """
        try:
            return _sanitize_str(v, "title", _MAX_TITLE_LENGTH)
        except Exception as exc:
            logger.exception("Title validation failed: %s", exc)
            raise ValueError("Invalid title") from exc

    @validator("owner")
    def _validate_owner(cls, v: str) -> str:
        """
        Validate the owner field.

        Parameters
        ----------
        v : str
            Raw owner string.

        Returns
        -------
        str
            Sanitised owner.

        Raises
        ------
        ValueError
            If the owner exceeds length limits.
        """
        try:
            return _sanitize_str(v, "owner", _MAX_OWNER_LENGTH)
        except Exception as exc:
            logger.exception("Owner validation failed: %s", exc)
            raise ValueError("Invalid owner") from exc


class BountyCreate(BountyBase):
    """
    Payload for creating a new bounty.

    Inherits all fields from :class:`BountyBase`. ``created_at`` is optional and
    will be set to ``datetime.utcnow()`` if omitted.
    """

    created_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        description="Timestamp of bounty creation (optional)",
    )


class BountyUpdate(BaseModel):
    """
    Payload for partially updating a bounty.

    All fields are optional; only supplied fields will be validated and
    applied.
    """

    site: Optional[str] = None
    reputation: Optional[int] = None
    question_id: Optional[int] = None
    answer_id: Optional[int] = None
    user_id: Optional[int] = None
    bounty_amount: Optional[float] = None
    view_count: Optional[int] = None
    answer_count: Optional[int] = None
    owner: Optional[str] = None
    tags: Optional[Union[str, List[object]]] = None
    title: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=False,
    )

    @validator("site", pre=True, always=True)
    def _validate_site(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            return _validate_site(v)
        except Exception as exc:
            logger.exception("Site validation failed: %s", exc)
            raise ValueError("Invalid site") from exc

    @validator("tags", pre=True, always=True)
    def _validate_tags(cls, v: Optional[Union[str, List[object]]]) -> Optional[List[str]]:
        if v is None:
            return v
        try:
            return _parse_tags_raw(v)
        except Exception as exc:
            logger.exception("Tag validation failed: %s", exc)
            raise ValueError("Invalid tags") from exc

    @validator("title", always=True)
    def _validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            return _sanitize_str(v, "title", _MAX_TITLE_LENGTH)
        except Exception as exc:
            logger.exception("Title validation failed: %s", exc)
            raise ValueError("Invalid title") from exc

    @validator("owner", always=True)
    def _validate_owner(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            return _sanitize_str(v, "owner", _MAX_OWNER_LENGTH)
        except Exception as exc:
            logger.exception("Owner validation failed: %s", exc)
            raise ValueError("Invalid owner") from exc


class BountyResponse(BountyBase):
    """
    Representation returned by the API after persisting a bounty.

    Includes the database identifier ``id``.
    """

    id: int = Field(..., description="Database primary key")
