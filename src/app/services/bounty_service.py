# src/app/services/bounty_service.py
"""Business logic for processing StackExchange OPEN_BOUNTY entries.

The service parses raw CSV‑style payloads, filters entries by a configurable
set of tags and computes derived fields (e.g. expiry date, hot flag).  It
returns a SQLAlchemy ``Bounty`` model ready for persistence by the repository
layer.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import List, Optional, Set

from sqlalchemy.exc import SQLAlchemyError

# Local imports – these modules are part of the same code‑base.
# They are expected to exist; the service does not import any concrete
# implementation details of the repository or the ORM model.
from app.models import Bounty  # type: ignore
from app.repositories.bounty_repository import BountyRepository  # type: ignore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedBounty:
    """Immutable representation of a parsed OPEN_BOUNTY line."""

    post_id: int
    site: str
    score: int
    creation_date: datetime.datetime
    bounty_amount: int
    owner_user_id: int
    reputation: float
    view_count: int
    answer_count: int
    owner: str
    tags: List[str]
    title: str


class BountyService:
    """Service layer that encapsulates all business rules for bounties."""

    def __init__(self, repository: BountyRepository) -> None:
        """
        Initialise the service with a concrete repository.

        Args:
            repository: An instance of :class:`BountyRepository` used for
                persisting and retrieving :class:`Bounty` objects.
        """
        self._repo = repository

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    async def process_raw_line(
        self,
        raw_line: str,
        required_tags: Optional[Set[str]] = None,
    ) -> Optional[Bounty]:
        """
        Parse, filter and enrich a raw CSV line, then store it.

        The method returns the persisted :class:`Bounty` instance when the line
        passes the tag filter; otherwise ``None`` is returned.

        Args:
            raw_line: Tab‑separated string received from the SN feed.
            required_tags: Optional set of tags that must be present for the
                bounty to be accepted. If ``None`` the filter is skipped.

        Returns:
            The persisted :class:`Bounty` model or ``None``.
        """
        try:
            parsed = self._parse_line(raw_line)
        except ValueError as exc:
            logger.error("Failed to parse bounty line: %s", exc, exc_info=True)
            return None

        if required_tags and not self._has_required_tags(parsed.tags, required_tags):
            logger.debug(
                "Bounty %s filtered out – missing required tags %s",
                parsed.post_id,
                required_tags,
            )
            return None

        bounty = self._build_bounty_model(parsed)

        try:
            persisted = await self._repo.save(bounty)
            logger.info("Persisted bounty %s (site=%s)", persisted.post_id, persisted.site)
            return persisted
        except SQLAlchemyError as exc:
            logger.error(
                "Database error while persisting bounty %s: %s",
                parsed.post_id,
                exc,
                exc_info=True,
            )
            return None

    # --------------------------------------------------------------------- #
    # Private helpers
    # --------------------------------------------------------------------- #
    @staticmethod
    def _parse_line(line: str) -> ParsedBounty:
        """
        Convert a raw tab‑separated line into a :class:`ParsedBounty`.

        Expected column order (based on the specification):
        0  post_id
        1  site
        2  score
        3  creation_date (Unix timestamp)
        4  bounty_amount
        5  owner_user_id
        6  reputation
        7  view_count
        8  answer_count
        9  owner (e.g. ``recent@math``)
        10 tags (comma‑separated)
        11 title (may contain spaces)

        Raises:
            ValueError: If the line does not contain the required number of
                columns or any field cannot be converted to the expected type.
        """
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 12:
            raise ValueError(f"Expected at least 12 columns, got {len(parts)}")

        try:
            post_id = int(parts[0])
            site = parts[1]
            score = int(parts[2])
            creation_ts = int(parts[3])
            creation_date = datetime.datetime.utcfromtimestamp(creation_ts)
            bounty_amount = int(parts[4])
            owner_user_id = int(parts[5])
            reputation = float(parts[6])
            view_count = int(parts[7])
            answer_count = int(parts[8])
            owner = parts[9]
            tags = [t.strip() for t in parts[10].split(",") if t.strip()]
            title = parts[11]
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Failed to cast fields: {exc}") from exc

        return ParsedBounty(
            post_id=post_id,
            site=site,
            score=score,
            creation_date=creation_date,
            bounty_amount=bounty_amount,
            owner_user_id=owner_user_id,
            reputation=reputation,
            view_count=view_count,
            answer_count=answer_count,
            owner=owner,
            tags=tags,
            title=title,
        )

    @staticmethod
    def _has_required_tags(bounty_tags: List[str], required: Set[str]) -> bool:
        """
        Determine whether *all* required tags are present in the bounty.

        Args:
            bounty_tags: List of tags extracted from the raw line.
            required: Set of tags that must be present.

        Returns:
            ``True`` if every tag in ``required`` is found in ``bounty_tags``.
        """
        return required.issubset(set(bounty_tags))

    @staticmethod
    def _compute_expiry_date(creation_date: datetime.datetime) -> datetime.datetime:
        """
        Compute the expiry date for a bounty.

        The StackExchange bounty window is 7 days from creation.

        Args:
            creation_date: UTC datetime when the bounty was created.

        Returns:
            UTC datetime representing the expiry moment.
        """
        return creation_date + datetime.timedelta(days=7)

    @staticmethod
    def _is_hot(tags: List[str]) -> bool:
        """
        Derive a boolean flag indicating a “hot” bounty.

        The presence of the ``HOT`` tag marks the bounty as hot.

        Args:
            tags: List of tags attached to the bounty.

        Returns:
            ``True`` if ``HOT`` is among the tags, otherwise ``False``.
        """
        return "HOT" in (t.upper() for t in tags)

    def _build_bounty_model(self, parsed: ParsedBounty) -> Bounty:
        """
        Transform a :class:`ParsedBounty` into the ORM model, adding derived
        fields.

        Args:
            parsed: Parsed representation of the raw line.

        Returns:
            An instance of :class:`Bounty` ready for persistence.
        """
        expiry_date = self._compute_expiry_date(parsed.creation_date)
        is_hot = self._is_hot(parsed.tags)

        bounty = Bounty(
            post_id=parsed.post_id,
            site=parsed.site,
            score=parsed.score,
            creation_date=parsed.creation_date,
            bounty_amount=parsed.bounty_amount,
            owner_user_id=parsed.owner_user_id,
            reputation=parsed.reputation,
            view_count=parsed.view_count,
            answer_count=parsed.answer_count,
            owner=parsed.owner,
            tags=",".join(parsed.tags),  # stored as CSV in DB
            title=parsed.title,
            expiry_date=expiry_date,
            is_hot=is_hot,
        )
        logger.debug(
            "Built Bounty model: post_id=%s, site=%s, expiry=%s, hot=%s",
            bounty.post_id,
            bounty.site,
            bounty.expiry_date,
            bounty.is_hot,
        )
        return bounty