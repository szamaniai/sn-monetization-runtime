# tests/unit/test_services.py
"""
Unit tests for the BountyService parsing logic and business rules.

The tests cover:
* Successful parsing of a valid CSV payload line.
* Detection of invalid payload formats.
* Business‑rule filtering based on tags (e.g. only ``OPEN_BOUNTY`` entries).
* Edge‑case handling for missing fields and malformed numeric values.

All tests are written with **pytest** and **pytest‑asyncio** to support async
service methods.  Logging is configured locally to aid debugging without
polluting the test output.
"""

from __future__ import annotations

import logging
from typing import List

import pytest
import pytest_asyncio
from pydantic import ValidationError

# Import the service under test.  Adjust the import path if the package
# layout differs.
from app.services.bounty_service import BountyService, Bounty

# --------------------------------------------------------------------------- #
# Logging configuration (only for the test suite)
# --------------------------------------------------------------------------- #
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)
LOGGER.addHandler(handler)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def bounty_service() -> BountyService:
    """
    Provides a fresh ``BountyService`` instance for each test.
    The service is instantiated with an in‑memory SQLite engine to avoid
    side‑effects on a real database.
    """
    service = BountyService(database_url="sqlite+aiosqlite:///:memory:")
    await service.init()  # type: ignore[arg-type] – async init for DB setup
    return service


# --------------------------------------------------------------------------- #
# Helper data
# --------------------------------------------------------------------------- #
VALID_LINE = (
    "1482916\tmath\t2\t1702\t1000\t7\t24.1\t48657\t13566\t"
    "recent@math\tOPEN_BOUNTY,HOT,SELF_POST_OPP\tWeekend Puzzle: Interesting Numbers"
)

INVALID_LINE_MISSING_FIELDS = "1482916\tmath\t2\t1702\t1000\t7\t24.1\t48657\t13566\trecent@math"
INVALID_LINE_BAD_NUMBER = (
    "1482916\tmath\tinvalid_int\t1702\t1000\t7\t24.1\t48657\t13566\t"
    "recent@math\tOPEN_BOUNTY,HOT,SELF_POST_OPP\tWeekend Puzzle: Interesting Numbers"
)

# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_parse_valid_line(bounty_service: BountyService) -> None:
    """
    ``parse_payload`` must correctly transform a well‑formed CSV line into a
    ``Bounty`` model.
    """
    bounty: Bounty = await bounty_service.parse_payload(VALID_LINE)

    assert bounty.id == 1482916
    assert bounty.site == "math"
    assert bounty.points == 2
    assert bounty.user_id == 1702
    assert bounty.reputation == 1000
    assert bounty.answer_count == 7
    assert bounty.duration_days == 24.1
    assert bounty.creation_ts == 48657
    assert bounty.last_activity_ts == 13566
    assert bounty.author == "recent@math"
    assert "OPEN_BOUNTY" in bounty.tags
    assert bounty.title == "Weekend Puzzle: Interesting Numbers"


@pytest.mark.asyncio
async def test_parse_invalid_missing_fields(bounty_service: BountyService) -> None:
    """
    Missing mandatory columns should raise a ``ValueError``.
    """
    with pytest.raises(ValueError) as exc_info:
        await bounty_service.parse_payload(INVALID_LINE_MISSING_FIELDS)

    LOGGER.debug("Caught expected exception: %s", exc_info.value)
    assert "expected 13 fields" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_parse_invalid_numeric_value(bounty_service: BountyService) -> None:
    """
    Non‑numeric values in integer columns must trigger a ``ValidationError``.
    """
    with pytest.raises(ValidationError) as exc_info:
        await bounty_service.parse_payload(INVALID_LINE_BAD_NUMBER)

    LOGGER.debug("Caught expected ValidationError: %s", exc_info.value)
    assert "value is not a valid integer" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_filter_open_bounty(bounty_service: BountyService) -> None:
    """
    ``is_open_bounty`` should return ``True`` only when the ``OPEN_BOUNTY``
    tag is present.
    """
    bounty = await bounty_service.parse_payload(VALID_LINE)
    assert bounty_service.is_open_bounty(bounty) is True

    # Remove the tag and verify the filter fails.
    bounty_no_tag = bounty.copy(update={"tags": ["HOT", "SELF_POST_OPP"]})
    assert bounty_service.is_open_bounty(bounty_no_tag) is False


@pytest.mark.asyncio
async def test_bulk_parsing_and_filtering(bounty_service: BountyService) -> None:
    """
    Simulate a batch of payload lines and ensure that only ``OPEN_BOUNTY``
    entries survive the filtering step.
    """
    payloads: List[str] = [
        VALID_LINE,
        VALID_LINE.replace("OPEN_BOUNTY", "CLOSED_BOUNTY"),
        INVALID_LINE_MISSING_FIELDS,
        VALID_LINE.replace("Weekend Puzzle: Interesting Numbers", "Another Bounty"),
    ]

    parsed: List[Bounty] = []
    for line in payloads:
        try:
            bounty = await bounty_service.parse_payload(line)
            parsed.append(bounty)
        except Exception as exc:  # noqa: BLE001 – intentional catch‑all for test flow
            LOGGER.debug("Skipping line due to error: %s", exc)

    open_bounties = [b for b in parsed if bounty_service.is_open_bounty(b)]

    assert len(open_bounties) == 2
    titles = {b.title for b in open_bounties}
    assert titles == {"Weekend Puzzle: Interesting Numbers", "Another Bounty"}