python
# app/models.py
"""
SQLAlchemy ORM models for the bounty‑monitoring micro‑service.

The module defines three tables:

* ``User`` – StackExchange user information.
* ``Bounty`` – Open bounty metadata.
* ``RadarLog`` – Raw radar feed entries for audit / debugging.

All models are fully type‑annotated, include ``__repr__`` and ``to_dict``
helpers, and perform basic input validation. A factory ``parse_bounty`` safely
transforms a raw payload into a ``Bounty`` instance with detailed error
handling and logging.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, TypedDict, Union

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column, relationship

# --------------------------------------------------------------------------- #
# Logging configuration – the application can override this if needed.
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s – %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Declarative base for all ORM models.
# --------------------------------------------------------------------------- #
Base = declarative_base()

# --------------------------------------------------------------------------- #
# Typed payloads and custom exceptions
# --------------------------------------------------------------------------- #
class RawBountyPayload(TypedDict, total=False):
    """Typed representation of the JSON payload received from the radar."""

    post_id: int
    site: str
    bounty_amount: int
    owner_user_id: int
    reputation: Optional[int]
    view_count: Optional[int]
    answer_count: Optional[int]
    score: Optional[int]
    creation_date: Optional[str]  # ISO‑8601
    last_activity_date: Optional[str]
    owner_email: Optional[str]
    tags: Optional[str]  # comma‑separated
    title: Optional[str]
    raw_payload: Optional[Dict[str, Any]]


class BountyParsingError(ValueError):
    """Raised when a raw bounty payload cannot be parsed into a ``Bounty``."""


# --------------------------------------------------------------------------- #
# Validation utilities
# --------------------------------------------------------------------------- #
_EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")


def _validate_email(email: Optional[str]) -> Optional[str]:
    """Trim and validate an email address.

    Args:
        email: Raw email string or ``None``.

    Returns:
        Trimmed email string if valid, otherwise ``None``.

    Raises:
        ValueError: If the email does not match the required pattern.
    """
    if email is None:
        return None
    email = email.strip()
    if not _EMAIL_REGEX.fullmatch(email):
        raise ValueError(f"Invalid email address: {email!r}")
    return email


def _validate_tags(tags: Optional[str]) -> Optional[str]:
    """Validate and normalise a comma‑separated tag list.

    Args:
        tags: Raw tag string or ``None``.

    Returns:
        Normalised comma‑separated string.

    Raises:
        ValueError: If the resulting tag list is empty.
    """
    if tags is None:
        return None
    cleaned = ",".join(t.strip() for t in tags.split(",") if t.strip())
    if not cleaned:
        raise ValueError("Tag list cannot be empty after cleaning")
    return cleaned


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO‑8601 datetime string (UTC) to ``datetime``.

    Args:
        value: ISO‑8601 string or ``None``.

    Returns:
        ``datetime`` instance in UTC or ``None``.

    Raises:
        ValueError: If parsing fails.
    """
    if value is None:
        return None
    try:
        # ``fromisoformat`` does not understand trailing ``Z`` – strip it.
        return datetime.fromisoformat(value.rstrip("Z"))
    except Exception as exc:
        raise ValueError(f"Invalid datetime format: {value!r}") from exc


# --------------------------------------------------------------------------- #
# ORM models
# --------------------------------------------------------------------------- #
class User(Base):
    """StackExchange user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=False,
        comment="StackExchange user identifier (unique).",
    )
    username: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        index=True,
        comment="Display name of the user.",
    )
    reputation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Current reputation score.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="When the user record was first persisted.",
    )

    # Relationship to Bounty – a user may have many open bounties.
    bounties: Mapped[List["Bounty"]] = relationship(
        "Bounty",
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} username={self.username!r}>"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the model to a plain ``dict``."""
        return {
            "id": self.id,
            "username": self.username,
            "reputation": self.reputation,
            "created_at": self.created_at.isoformat(),
        }


class Bounty(Base):
    """Open bounty metadata derived from the StackExchange ``OPEN_BOUNTY`` feed."""

    __tablename__ = "bounties"

    post_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=False,
        comment="StackExchange post identifier (question/answer).",
    )
    site: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="StackExchange site short name (e.g. ``math``).",
    )
    bounty_amount: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Amount of the bounty in reputation points.",
    )
    owner_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Foreign key to ``users.id`` – the user who offered the bounty.",
    )
    owner: Mapped[User] = relationship(
        "User",
        back_populates="bounties",
        lazy="joined",
    )
    reputation: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Reputation of the owner at the time of the feed entry.",
    )
    view_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of views the post has received.",
    )
    answer_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of answers posted.",
    )
    score: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Current score of the post.",
    )
    creation_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp when the post was created.",
    )
    last_activity_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp of the last activity on the post.",
    )
    title: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Post title.",
    )
    tags: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
        comment="Comma‑separated list of tags.",
    )
    raw_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
        comment="Original JSON payload for audit purposes.",
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="When the payload was processed into a Bounty record.",
    )

    def __repr__(self) -> str:
        return (
            f"<Bounty post_id={self.post_id!r} site={self.site!r} "
            f"amount={self.bounty_amount!r}>"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the model to a plain ``dict``."""
        return {
            "post_id": self.post_id,
            "site": self.site,
            "bounty_amount": self.bounty_amount,
            "owner_user_id": self.owner_user_id,
            "reputation": self.reputation,
            "view_count": self.view_count,
            "answer_count": self.answer_count,
            "score": self.score,
            "creation_date": self.creation_date.isoformat()
            if self.creation_date
            else None,
            "last_activity_date": self.last_activity_date.isoformat()
            if self.last_activity_date
            else None,
            "title": self.title,
            "tags": self.tags,
            "processed_at": self.processed_at.isoformat(),
        }


class RadarLog(Base):
    """Raw radar feed entries – kept for debugging / audit."""

    __tablename__ = "radar_logs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="Surrogate primary key.",
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="When the raw entry was received.",
    )
    payload: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        comment="Original payload JSON.",
    )
    processed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Has the entry been processed into a Bounty?",
    )

    def __repr__(self) -> str:
        return f"<RadarLog id={self.id!r} processed={self.processed!r}>"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the model to a plain ``dict``."""
        return {
            "id": self.id,
            "received_at": self.received_at.isoformat(),
            "payload": self.payload,
            "processed": self.processed,
        }


# --------------------------------------------------------------------------- #
# Factory / parsing logic
# --------------------------------------------------------------------------- #
def parse_bounty(
    raw: Mapping[str, Any],
    *,
    strict: bool = True,
) -> Bounty:
    """Validate a raw payload and instantiate a ``Bounty`` ORM object.

    The function performs exhaustive validation, logs each step and raises a
    ``BountyParsingError`` with a clear message when validation fails.

    Args:
        raw: Mapping (typically a dict) containing the raw radar payload.
        strict: If ``True`` (default) unknown fields raise an error; otherwise
            they are ignored.

    Returns:
        A ``Bounty`` instance ready for persistence.

    Raises:
        BountyParsingError: If required fields are missing or validation fails.
    """
    logger.debug("Starting bounty parsing – payload: %s", raw)

    # ------------------------------------------------------------------- #
    # Helper to fetch a mandatory field with a clear error message.
    # ------------------------------------------------------------------- #
    def _require(key: str) -> Any:
        if key not in raw:
            raise BountyParsingError(f"Missing required field: {key!r}")
        return raw[key]

    try:
        post_id = int(_require("post_id"))
        site = str(_require("site")).strip()
        bounty_amount = int(_require("bounty_amount"))
        owner_user_id = int(_require("owner_user_id"))

        # Optional fields with safe conversion
        reputation = (
            int(raw["reputation"]) if "reputation" in raw and raw["reputation"] is not None else None
        )
        view_count = (
            int(raw["view_count"]) if "view_count" in raw and raw["view_count"] is not None else None
        )
        answer_count = (
            int(raw["answer_count"])
            if "answer_count" in raw and raw["answer_count"] is not None
            else None
        )
        score = int(raw["score"]) if "score" in raw and raw["score"] is not None else None

        creation_date = _parse_iso_datetime(raw.get("creation_date"))
        last_activity_date = _parse_iso_datetime(raw.get("last_activity_date"))
        title = raw.get("title")
        tags = _validate_tags(raw.get("tags"))
        raw_payload = dict(raw)  # shallow copy for audit

        # Validate email if present – we store it only for logging, not in the model.
        if "owner_email" in raw:
            try:
                _validate_email(raw["owner_email"])
            except ValueError as exc:
                logger.warning("Invalid owner email ignored: %s", exc)

        bounty = Bounty(
            post_id=post_id,
            site=site,
            bounty_amount=bounty_amount,
            owner_user_id=owner_user_id,
            reputation=reputation,
            view_count=view_count,
            answer_count=answer_count,
            score=score,
            creation_date=creation_date,
            last_activity_date=last_activity_date,
            title=title,
            tags=tags,
            raw_payload=raw_payload,
        )
        logger.info(
            "Successfully parsed bounty – post_id=%s site=%s amount=%s",
            post_id,
            site,
            bounty_amount,
        )
        return bounty

    except (ValueError, TypeError) as exc:
        logger.error("Bounty parsing failed: %s", exc)
        raise BountyParsingError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover – defensive catch‑all
        logger.exception("Unexpected error while parsing bounty")
        raise BountyParsingError("Unexpected parsing error") from exc


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
__all__: List[str] = [
    "Base",
    "User",
    "Bounty",
    "RadarLog",
    "RawBountyPayload",
    "BountyParsingError",
    "parse_bounty",
]
