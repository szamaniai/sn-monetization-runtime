# src/app/models.py
"""
SQLAlchemy ORM definitions for the bounty microservice.

Provides:
- ``Bounty`` – main entity representing an OPEN_BOUNTY entry.
- ``Tag`` – reusable tag entity.
- ``bounty_tag`` – many‑to‑many association table.
- ``parse_bounty_csv`` – helper to convert a CSV line into a ``Bounty`` instance.

All models include type hints, sensible defaults, and rich ``__repr__`` for debugging.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import List, Sequence

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# --------------------------------------------------------------------------- #
# Logging configuration (the application can override the level globally)
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Base class for all declarative models
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    """Base class for all ORM models."""

    __abstract__ = True


# --------------------------------------------------------------------------- #
# Association table for many‑to‑many relationship between Bounty and Tag
# --------------------------------------------------------------------------- #
bounty_tag: Table = Table(
    "bounty_tag",
    Base.metadata,
    Column("bounty_id", Integer, ForeignKey("bounties.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    UniqueConstraint("bounty_id", "tag_id", name="uq_bounty_tag"),
)


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class BountyStatus(str, Enum):
    """Possible statuses of a bounty entry."""

    OPEN = "OPEN_BOUNTY"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    DELETED = "DELETED"


# --------------------------------------------------------------------------- #
# Tag model
# --------------------------------------------------------------------------- #
class Tag(Base):
    """Tag that can be attached to a bounty."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Back‑reference to bounties (read‑only)
    bounties: Mapped[List["Bounty"]] = relationship(
        "Bounty",
        secondary=bounty_tag,
        back_populates="tags",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Tag id={self.id!r} name={self.name!r}>"

    def __str__(self) -> str:
        return self.name


# --------------------------------------------------------------------------- #
# Bounty model
# --------------------------------------------------------------------------- #
class Bounty(Base):
    """SQLAlchemy model representing a StackExchange OPEN_BOUNTY entry."""

    __tablename__ = "bounties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Raw identifier from the feed (e.g., 1482916)
    feed_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)

    # Site name (e.g., "math")
    site: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # User identifier that offered the bounty
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Amount of the bounty in reputation points
    amount: Mapped[int] = mapped_column(Integer, nullable=False)

    # Number of answers the bounty is attached to (optional, may be zero)
    answer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Reputation score of the owner at the moment of posting
    owner_reputation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Average rating of the question (float, optional)
    rating: Mapped[float] = mapped_column(Float, nullable=True)

    # View count of the question
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Answer count of the question
    question_answer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Owner e‑mail or identifier (e.g., "recent@math")
    owner: Mapped[str] = mapped_column(String(128), nullable=False)

    # Current status of the bounty
    status: Mapped[BountyStatus] = mapped_column(SAEnum(BountyStatus), nullable=False, default=BountyStatus.OPEN)

    # Human‑readable title of the question
    title: Mapped[str] = mapped_column(String(256), nullable=False)

    # Timestamp when the record was first stored
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Timestamp of the last update (e.g., when status changes)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship to tags (many‑to‑many)
    tags: Mapped[List[Tag]] = relationship(
        "Tag",
        secondary=bounty_tag,
        back_populates="bounties",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Bounty id={self.id!r} feed_id={self.feed_id!r} site={self.site!r} "
            f"status={self.status!r} amount={self.amount!r}>"
        )

    def __str__(self) -> str:
        return f"{self.site.title()} – {self.title}"


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _parse_int(value: str, field_name: str) -> int:
    """Parse an integer from a string, raising a detailed ValueError on failure."""
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Unable to parse integer for field '{field_name}': {value!r}") from exc


def _parse_float(value: str, field_name: str) -> float:
    """Parse a float from a string, raising a detailed ValueError on failure."""
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Unable to parse float for field '{field_name}': {value!r}") from exc


def parse_bounty_csv(csv_line: str) -> Bounty:
    """
    Convert a CSV‑style line from the SN feed into a :class:`Bounty` instance.

    Expected column order (as seen in the example):
        feed_id,site,user_id,answer_count,owner_reputation,
        rating,view_count,question_answer_count,owner,statuses,title

    ``statuses`` is a comma‑separated list; the first token determines the
    :class:`BountyStatus`. If none match, ``OPEN_BOUNTY`` is used as a fallback.

    Parameters
    ----------
    csv_line:
        Raw CSV line (without trailing newline).

    Returns
    -------
    Bounty
        An unsaved ``Bounty`` object ready for ``session.add()``.

    Raises
    ------
    ValueError
        If the line does not contain the expected number of columns or a
        conversion fails.
    """
    logger.debug("Parsing CSV line: %s", csv_line)
    parts: List[str] = [p.strip() for p in csv_line.split("\t")]
    if len(parts) < 11:
        raise ValueError(f"Expected at least 11 columns, got {len(parts)}: {csv_line!r}")

    (
        feed_id_str,
        site,
        user_id_str,
        answer_count_str,
        owner_reputation_str,
        rating_str,
        view_count_str,
        question_answer_count_str,
        owner,
        statuses_str,
        title,
        *extra,
    ) = parts

    # Convert numeric fields with robust error handling
    feed_id = _parse_int(feed_id_str, "feed_id")
    user_id = _parse_int(user_id_str, "user_id")
    answer_count = _parse_int(answer_count_str, "answer_count")
    owner_reputation = _parse_int(owner_reputation_str, "owner_reputation")
    view_count = _parse_int(view_count_str, "view_count")
    question_answer_count = _parse_int(question_answer_count_str, "question_answer_count")
    rating = _parse_float(rating_str, "rating") if rating_str else None

    # Determine status – the first known token wins
    status_token = next((s for s in statuses_str.split(",") if s), "OPEN_BOUNTY")
    try:
        status = BountyStatus(status_token)
    except ValueError:
        logger.warning("Unrecognised status token %s – defaulting to OPEN_BOUNTY", status_token)
        status = BountyStatus.OPEN

    # Split tags – empty strings are ignored
    tag_names = [t for t in statuses_str.split(",") if t and t not in BountyStatus.__members__.values()]

    # Build Bounty instance (tags are attached later by the service layer)
    bounty = Bounty(
        feed_id=feed_id,
        site=site,
        user_id=user_id,
        answer_count=answer_count,
        owner_reputation=owner_reputation,
        rating=rating,
        view_count=view_count,
        question_answer_count=question_answer_count,
        owner=owner,
        status=status,
        title=title,
    )

    # Attach tags as Tag objects (service layer should deduplicate / fetch existing tags)
    bounty.tags = [Tag(name=name) for name in tag_names]

    logger.debug("Created Bounty object: %s", bounty)
    return bounty


__ --------------------------------------------------------------------------- #
# Public API of this module
# --------------------------------------------------------------------------- #
__all__: Sequence[str] = (
    "Base",
    "Bounty",
    "Tag",
    "bounty_tag",
    "BountyStatus",
    "parse_bounty_csv",
)