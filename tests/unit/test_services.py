"""
tests/unit/test_services.py

Unit tests for the ``bounty_service`` and ``radar_client`` modules.
The tests cover normal operation, error handling and edge‑cases
using ``pytest`` and ``pytest‑asyncio``. All external dependencies
(e.g. HTTP calls, database sessions, Redis) are mocked to keep the
tests fast and deterministic.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

import pytest
import pytest_asyncio
from httpx import AsyncClient, Response
from unittest.mock import AsyncMock, MagicMock, patch

# --------------------------------------------------------------------------- #
# Configure a module‑level logger for the test suite
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s – %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


# --------------------------------------------------------------------------- #
# Test data – a realistic Open Bounty line from the SN feed
# --------------------------------------------------------------------------- #
OPEN_BOUNTY_LINE = (
    "1482916\tmath\t2\t1702\t1000\t7\t24.1\t48657\t13566\t"
    "recent@math\tOPEN_BOUNTY,HOT,SELF_POST_OPP\tWeekend Puzzle: Interesting Numbers"
)

EXPECTED_BOUNTY_DICT: Dict[str, Any] = {
    "post_id": 1482916,
    "site": "math",
    "score": 2,
    "answer_count": 1702,
    "bounty_amount": 1000,
    "bounty_start": 7,
    "bounty_end": 24.1,
    "question_id": 48657,
    "owner_id": 13566,
    "owner_name": "recent@math",
    "tags": ["OPEN_BOUNTY", "HOT", "SELF_POST_OPP"],
    "title": "Weekend Puzzle: Interesting Numbers",
    "detected_at": datetime.utcnow(),
}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def async_http_client() -> AsyncClient:
    """Provide an ``httpx.AsyncClient`` instance for the duration of a test."""
    async with AsyncClient() as client:
        yield client


@pytest.fixture
def mock_redis() -> MagicMock:
    """Mock a Redis client used by the service."""
    redis_mock = MagicMock()
    redis_mock.publish = MagicMock()
    return redis_mock


@pytest.fixture
def mock_db_session() -> MagicMock:
    """Mock a SQLAlchemy session."""
    session = MagicMock()
    session.add = MagicMock()
    session.commit = MagicMock()
    session.refresh = MagicMock()
    session.query = MagicMock()
    return session


# --------------------------------------------------------------------------- #
# Helper – parse a tab‑separated bounty line (implementation under test)
# --------------------------------------------------------------------------- #
def _parse_bounty_line(line: str) -> Dict[str, Any]:
    """
    Parse a raw Open Bounty line from the SN feed.

    The function is deliberately simple; it mirrors the behaviour of
    ``radar_client.parse_bounty_line`` which is exercised by the tests.
    """
    try:
        parts = line.split("\t")
        if len(parts) != 12:
            raise ValueError("Unexpected number of fields")

        post_id = int(parts[0])
        site = parts[1]
        score = int(parts[2])
        answer_count = int(parts[3])
        bounty_amount = int(parts[4])
        bounty_start = int(parts[5])
        bounty_end = float(parts[6])
        question_id = int(parts[7])
        owner_id = int(parts[8])
        owner_name = parts[9]
        tags = parts[10].split(",")
        title = parts[11]

        return {
            "post_id": post_id,
            "site": site,
            "score": score,
            "answer_count": answer_count,
            "bounty_amount": bounty_amount,
            "bounty_start": bounty_start,
            "bounty_end": bounty_end,
            "question_id": question_id,
            "owner_id": owner_id,
            "owner_name": owner_name,
            "tags": tags,
            "title": title,
            "detected_at": datetime.utcnow(),
        }
    except Exception as exc:
        logger.exception("Failed to parse bounty line")
        raise ValueError(f"Invalid bounty line: {exc}") from exc


# --------------------------------------------------------------------------- #
# Tests for radar_client
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_radar_client_fetch_success(
    async_http_client: AsyncClient,
    mock_redis: MagicMock,
) -> None:
    """
    Verify that ``radar_client.fetch_open_bounties`` correctly retrieves data,
    parses it and publishes the result to Redis.
    """
    from bounty_service import radar_client

    # Mock the HTTP GET request performed inside ``radar_client``
    mock_response = Response(
        status_code=200,
        content=json.dumps({"bounties": [OPEN_BOUNTY_LINE]}).encode(),
        request=MagicMock(),
    )
    with patch.object(async_http_client, "get", return_value=mock_response):
        # Patch the internal client creation to use our fixture
        with patch.object(radar_client, "http_client", async_http_client):
            # Patch the parser to use the local helper (ensures deterministic output)
            with patch.object(radar_client, "parse_bounty_line", side_effect=_parse_bounty_line):
                # Execute the function under test
                await radar_client.fetch_open_bounties(redis_client=mock_redis)

    # Assertions
    mock_redis.publish.assert_called_once()
    args, _ = mock_redis.publish.call_args
    channel, payload = args
    assert channel == "bounty_updates"
    parsed = json.loads(payload)
    assert parsed["post_id"] == EXPECTED_BOUNTY_DICT["post_id"]
    assert "detected_at" in parsed


@pytest.mark.asyncio
async def test_radar_client_fetch_http_error(
    async_http_client: AsyncClient,
    mock_redis: MagicMock,
) -> None:
    """
    Ensure that a non‑200 response raises an informative exception and does
    not publish anything to Redis.
    """
    from bounty_service import radar_client

    mock_response = Response(status_code=503, content=b"", request=MagicMock())
    with patch.object(async_http_client, "get", return_value=mock_response):
        with patch.object(radar_client, "http_client", async_http_client):
            with pytest.raises(RuntimeError, match="Failed to fetch bounty feed"):
                await radar_client.fetch_open_bounties(redis_client=mock_redis)

    mock_redis.publish.assert_not_called()


# --------------------------------------------------------------------------- #
# Tests for bounty_service (CRUD operations)
# --------------------------------------------------------------------------- #
def test_create_bounty_success(mock_db_session: MagicMock) -> None:
    """
    ``bounty_service.create_bounty`` should add a new bounty record,
    commit the transaction and return the persisted model.
    """
    from bounty_service import bounty_service, models

    # Prepare a minimal Pydantic model that the service expects
    bounty_input = models.BountyCreate(
        post_id=EXPECTED_BOUNTY_DICT["post_id"],
        site=EXPECTED_BOUNTY_DICT["site"],
        title=EXPECTED_BOUNTY_DICT["title"],
        bounty_amount=EXPECTED_BOUNTY_DICT["bounty_amount"],
        tags=EXPECTED_BOUNTY_DICT["tags"],
    )

    # Mock the ORM model that will be instantiated inside the service
    with patch.object(models, "Bounty", autospec=True) as mock_orm_cls:
        mock_orm_instance = mock_orm_cls.return_value
        mock_orm_instance.id = 1  # Simulate DB‑generated primary key

        result = bounty_service.create_bounty(bounty_input, db=mock_db_session)

    # Verify DB interactions
    mock_db_session.add.assert_called_once_with(mock_orm_instance)
    mock_db_session.commit.assert_called_once()
    mock_db_session.refresh.assert_called_once_with(mock_orm_instance)

    # Verify the returned object matches the mock instance
    assert result.id == 1
    assert result.post_id == bounty_input.post_id


def test_get_bounty_not_found(mock_db_session: MagicMock) -> None:
    """
    ``bounty_service.get_bounty`` must raise ``ValueError`` when the bounty
    does not exist.
    """
    from bounty_service import bounty_service, models

    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = None
    mock_db_session.query.return_value = mock_query

    with pytest.raises(ValueError, match="Bounty not found"):
        bounty_service.get_bounty(bounty_id=999, db=mock_db_session)


@pytest.mark.asyncio
async def test_update_bounty_success(
    mock_db_session: MagicMock,
    async_http_client: AsyncClient,
) -> None:
    """
    ``bounty_service.update_bounty`` should modify the existing record,
    commit the transaction and return the updated model.
    """
    from bounty_service import bounty_service, models

    # Existing DB object
    existing = models.Bounty(
        id=42,
        post_id=1482916,
        site="math",
        title="Old title",
        bounty_amount=500,
        tags=["OPEN_BOUNTY"],
    )
    mock_db_session.query.return_value.filter.return_value.first.return_value = existing

    # Update payload
    update_data = models.BountyUpdate(title="Weekend Puzzle: Interesting Numbers", bounty_amount=1000)

    result = await bounty_service.update_bounty(
        bounty_id=42,
        bounty_update=update_data,
        db=mock_db_session,
    )

    # Verify fields were updated
    assert result.title == update_data.title
    assert result.bounty_amount == update_data.bounty_amount

    # Verify DB commit
    mock_db_session.commit.assert_called_once()
    mock_db_session.refresh.assert_called_once_with(existing)


def test_delete_bounty_success(mock_db_session: MagicMock) -> None:
    """
    ``bounty_service.delete_bounty`` must delete the record and commit.
    """
    from bounty_service import bounty_service, models

    bounty_obj = models.Bounty(id=10, post_id=12345, site="math", title="To delete", bounty_amount=200, tags=[])
    mock_query = MagicMock()
    mock_query.filter.return_value.first.return_value = bounty_obj
    mock_db_session.query.return_value = mock_query

    bounty_service.delete_bounty(bounty_id=10, db=mock_db_session)

    mock_db_session.delete.assert_called_once_with(bounty_obj)
    mock_db_session.commit.assert_called_once()


# --------------------------------------------------------------------------- #
# End‑to‑end style test using the FastAPI test client
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def fastapi_test_client() -> AsyncClient:
    """Create a test client that talks to the FastAPI app."""
    from bounty_service import app  # FastAPI instance

    async with AsyncClient(app=app, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_api_create_and_get_bounty(
    fastapi_test_client: AsyncClient,
    mock_db_session: MagicMock,
) -> None:
    """
    End‑to‑end test of the ``/bounties`` POST and GET endpoints.
    The DB session is patched so the API uses the mock.
    """
    from bounty_service import dependencies

    # Patch the DB dependency used by the FastAPI routes
    def get_test_db():
        return mock_db_session

    app = dependencies.get_app()
    app.dependency_overrides[dependencies.get_db] = get_test_db

    # Create a bounty via the API
    payload = {
        "post_id": EXPECTED_BOUNTY_DICT["post_id"],
        "site": EXPECTED_BOUNTY_DICT["site"],
        "title": EXPECTED_BOUNTY_DICT["title"],
        "bounty_amount": EXPECTED_BOUNTY_DICT["bounty_amount"],
        "tags": EXPECTED_BOUNTY_DICT["tags"],
    }
    response = await fastapi_test_client.post("/bounties/", json=payload)
    assert response.status_code == 201
    created = response.json()
    assert created["post_id"] == payload["post_id"]

    # Mock the DB query for the GET endpoint
    mock_bounty = MagicMock()
    mock_bounty.id = created["id"]
    mock_bounty.post_id = payload["post_id"]
    mock_bounty.site = payload["site"]
    mock_bounty.title = payload["title"]
    mock_bounty.bounty_amount = payload["bounty_amount"]
    mock_bounty.tags = payload["tags"]
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_bounty

    # Retrieve the same bounty via the API
    get_resp = await fastapi_test_client.get(f"/bounties/{created['id']}")
    assert get_resp.status_code == 200
    fetched = get_resp.json()
    assert fetched["post_id"] == payload["post_id"]
    assert fetched["title"] == payload["title"]