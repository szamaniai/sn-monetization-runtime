python
# app/services/radar_client.py
"""
Radar client – fetches and normalises open‑bounty rows from the StackExchange Radar API.

Typical usage
-------------
>>> from app.services.radar_client import RadarClient
>>> client = RadarClient()
>>> for bounty in client.fetch_open_bounties():
...     print(bounty.title)
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import List, Sequence

import httpx
from pydantic import BaseModel, Field, validator

# --------------------------------------------------------------------------- #
# Logging configuration (application may override via RADAR_CLIENT_LOG_LEVEL)
# --------------------------------------------------------------------------- #
LOGGER = logging.getLogger(__name__)
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
    LOGGER.setLevel(os.getenv("RADAR_CLIENT_LOG_LEVEL", "INFO"))

# --------------------------------------------------------------------------- #
# Domain model – immutable, validated representation of a bounty row
# --------------------------------------------------------------------------- #
class Bounty(BaseModel):
    """Immutable, validated representation of an Open‑Bounty row."""

    bounty_id: int = Field(..., description="Unique bounty identifier")
    site: str = Field(..., description="StackExchange site short name")
    score: int = Field(..., description="Current score of the bounty")
    reputation: int = Field(..., description="User reputation at bounty creation")
    bounty_amount: int = Field(..., description="Bounty amount in points")
    answer_count: int = Field(..., description="Number of answers posted")
    avg_answer_score: float = Field(..., description="Average answer score")
    view_count: int = Field(..., description="Number of views")
    favorite_count: int = Field(..., description="Number of favorites")
    owner: str = Field(..., description="Owner identifier (email or username)")
    tags: List[str] = Field(..., description="List of tags")
    title: str = Field(..., description="Bounty title")

    @validator("tags", pre=True)
    def _split_tags(cls, value: str) -> List[str]:
        """Split a comma‑separated tag string into a list."""
        return [t.strip() for t in value.split(",") if t.strip()]

    @validator("bounty_amount")
    def _positive_amount(cls, value: int) -> int:
        """Ensure bounty amount is non‑negative."""
        if value < 0:
            raise ValueError("bounty_amount must be >= 0")
        return value

    @validator("owner")
    def _owner_sanitise(cls, value: str) -> str:
        """Strip whitespace and enforce a max length for security."""
        cleaned = value.strip()
        if len(cleaned) > 256:
            raise ValueError("owner field exceeds maximum length")
        return cleaned

    class Config:
        frozen = True
        allow_mutation = False
        extra = "ignore"


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class RadarClientError(RuntimeError):
    """Base exception for all RadarClient‑related failures."""


class RadarNetworkError(RadarClientError):
    """Raised when a network request fails."""


class RadarParsingError(RadarClientError):
    """Raised when payload parsing or validation fails."""


# --------------------------------------------------------------------------- #
# Radar client implementation
# --------------------------------------------------------------------------- #
class RadarClient:
    """
    Synchronous wrapper for the Radar “open‑bounty” API.

    The implementation favours safety and observability over raw speed.
    All network interactions are performed with a short timeout and a
    deterministic retry policy.
    """

    DEFAULT_ENDPOINT: str = "https://radar.stackexchange.com/api/open-bounties"
    DEFAULT_TIMEOUT: float = 10.0  # seconds
    MAX_RETRIES: int = 3
    _HTTPS_PATTERN = re.compile(r"^https://", re.IGNORECASE)

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """
        Initialise the client.

        Args:
            endpoint: Radar API endpoint; defaults to the official URL.
            api_key: Optional bearer token for authenticated access.
            timeout: Request timeout in seconds.

        Raises:
            ValueError: If the supplied endpoint is not a valid HTTPS URL.
        """
        self.endpoint: str = endpoint or self.DEFAULT_ENDPOINT
        if not self._HTTPS_PATTERN.match(self.endpoint):
            raise ValueError("Radar endpoint must be an HTTPS URL")
        self.api_key: str | None = api_key or os.getenv("RADAR_API_KEY")
        self.timeout: float = timeout or self.DEFAULT_TIMEOUT

        LOGGER.debug(
            "RadarClient initialised – endpoint=%s, timeout=%s",
            self.endpoint,
            self.timeout,
        )

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def fetch_open_bounties(self) -> List[Bounty]:
        """
        Retrieve the current list of open‑bounty rows from Radar.

        Returns:
            A list of validated ``Bounty`` objects.

        Raises:
            RadarNetworkError: If the HTTP request fails after retries.
            RadarParsingError: If parsing or validation fails.
        """
        raw_lines = self._download_payload()
        return self._parse_payload(raw_lines)

    # --------------------------------------------------------------------- #
    # Private helpers
    # --------------------------------------------------------------------- #
    def _download_payload(self) -> List[str]:
        """
        Download the raw CSV‑like payload from the Radar endpoint.

        Returns:
            A list of raw lines (strings) without trailing newlines.

        Raises:
            RadarNetworkError: If the HTTP request fails after the configured
                number of retries.
        """
        headers: dict[str, str] = {}
        if self.api_key:
            LOGGER.debug("Using provided API key for authentication")
            headers["Authorization"] = f"Bearer {self.api_key}"

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                LOGGER.debug("Attempt %d – GET %s", attempt, self.endpoint)
                response = httpx.get(
                    self.endpoint,
                    headers=headers,
                    timeout=self.timeout,
                    follow_redirects=True,
                    http2=True,
                )
                response.raise_for_status()
                payload = response.text.strip()
                lines = payload.splitlines()
                LOGGER.info("Fetched %d raw bounty rows", len(lines))
                return lines
            except httpx.HTTPError as exc:
                LOGGER.warning("Attempt %d failed – %s", attempt, exc)
                if attempt == self.MAX_RETRIES:
                    raise RadarNetworkError(
                        f"Failed to fetch bounty data after {self.MAX_RETRIES} attempts"
                    ) from exc
        # Unreachable – loop either returns or raises.
        raise RadarNetworkError("Unexpected exit from download loop")

    def _parse_payload(self, lines: Sequence[str]) -> List[Bounty]:
        """
        Convert raw CSV lines into ``Bounty`` objects.

        Args:
            lines: Sequence of raw CSV strings as returned by Radar.

        Returns:
            List of ``Bounty`` instances.

        Raises:
            RadarParsingError: If parsing fails or validation errors occur.
        """
        if not lines:
            LOGGER.warning("No bounty data received from Radar")
            return []

        # Expected column order based on the example payload
        expected_fields = (
            "bounty_id",
            "site",
            "score",
            "reputation",
            "bounty_amount",
            "answer_count",
            "avg_answer_score",
            "view_count",
            "favorite_count",
            "owner",
            "tags",
            "title",
        )

        bounties: List[Bounty] = []
        csv_reader = csv.reader(lines, delimiter="\t")
        for line_no, row in enumerate(csv_reader, start=1):
            if len(row) != len(expected_fields):
                LOGGER.error(
                    "Line %d has %d columns (expected %d)",
                    line_no,
                    len(row),
                    len(expected_fields),
                )
                raise RadarParsingError(
                    f"Malformed row {line_no}: unexpected column count"
                )
            # Map fields to a dict for Pydantic validation
            row_dict = dict(zip(expected_fields, row))
            try:
                bounty = Bounty(**row_dict)
                bounties.append(bounty)
            except ValueError as exc:
                LOGGER.error(
                    "Validation error on line %d – %s", line_no, exc
                )
                raise RadarParsingError(
                    f"Validation failed for row {line_no}"
                ) from exc

        LOGGER.info("Parsed %d valid bounty objects", len(bounties))
        return bounties
