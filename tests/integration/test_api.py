"""
Integration tests for the FastAPI bounty service.

The tests exercise the full request/response flow using FastAPI's TestClient.
They verify that the service starts correctly, that the health endpoint is
available, and that the bounty CRUD endpoints behave as expected.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Logging configuration (module‑level, shared by all tests)
# --------------------------------------------------------------------------- #
LOGGER_NAME = "integration.tests"
logger = logging.getLogger(LOGGER_NAME)
if not logger.handlers:  # Prevent duplicate handlers when re‑imported
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def client() -> TestClient:
    """
    Create a TestClient instance bound to the FastAPI application.

    The FastAPI app is expected to be defined in ``src/main.py`` and
    exported as a module‑level variable named ``app``.
    """
    try:
        from src.main import app  # pylint: disable=import-error
    except Exception as exc:
        logger.error("Failed to import FastAPI application: %s", exc)
        raise

    return TestClient(app)


@pytest.fixture
def sample_bounty() -> Dict[str, Any]:
    """
    Return a minimal but realistic bounty payload that matches the
    ``OPEN_BOUNTY`` CSV format used by the background worker.
    """
    return {
        "question_id": 1482916,
        "site": "math",
        "bounty_amount": 2,
        "creation_date": 1702,
        "expiration_date": 1000,
        "score": 7,
        "tags": ["24.1"],
        "view_count": 48657,
        "answer_count": 13566,
        "owner": "recent@math",
        "labels": ["OPEN_BOUNTY", "HOT", "SELF_POST_OPP"],
        "title": "Weekend Puzzle: Interesting Numbers",
    }


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def _log_response(response: Any) -> None:
    """Log request/response details for debugging."""
    logger.debug(
        "Request: %s %s\nResponse (%s): %s",
        response.request.method,
        response.request.url,
        response.status_code,
        response.text,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_health_endpoint(client: TestClient) -> None:
    """
    Verify that the health check endpoint returns a JSON payload with ``status``.
    """
    response = client.get("/health")
    _log_response(response)

    assert response.status_code == 200, "Health endpoint did not return 200"
    data = response.json()
    assert isinstance(data, dict), "Health response is not a JSON object"
    assert data.get("status") == "ok", "Health status is not 'ok'"


def test_get_empty_bounties(client: TestClient) -> None:
    """
    When no bounties have been stored, the ``/bounties`` endpoint should
    return an empty list.
    """
    response = client.get("/bounties")
    _log_response(response)

    assert response.status_code == 200, "GET /bounties did not return 200"
    data = response.json()
    assert isinstance(data, list), "Bounties response is not a list"
    assert len(data) == 0, "Expected empty bounty list"


def test_create_and_retrieve_bounty(
    client: TestClient, sample_bounty: Dict[str, Any]
) -> None:
    """
    Create a bounty via ``POST /bounties`` and then retrieve it using
    ``GET /bounties/{question_id}``.
    """
    # ------------------------------------------------------------------- #
    # Create
    # ------------------------------------------------------------------- #
    create_resp = client.post(
        "/bounties",
        data=json.dumps(sample_bounty),
        headers={"Content-Type": "application/json"},
    )
    _log_response(create_resp)

    assert create_resp.status_code == 201, "Bounty creation failed"
    created = create_resp.json()
    assert created.get("question_id") == sample_bounty["question_id"]

    # ------------------------------------------------------------------- #
    # Retrieve
    # ------------------------------------------------------------------- #
    retrieve_resp = client.get(f"/bounties/{sample_bounty['question_id']}")
    _log_response(retrieve_resp)

    assert retrieve_resp.status_code == 200, "Bounty retrieval failed"
    retrieved = retrieve_resp.json()
    for key, value in sample_bounty.items():
        assert retrieved.get(key) == value, f"Mismatch on field {key}"


def test_invalid_bounty_payload(client: TestClient) -> None:
    """
    Sending a malformed payload should result in a ``422 Unprocessable Entity``
    response generatedFastAPI/Pydantic validation error).
    """
    malformed = {"question_id": "not-an-int", "site": 123}
    response = client.post(
        "/bounties",
        data=json.dumps(malformed),
        headers={"Content-Type": "application/json"},
    )
    _log_response(response)

    assert response.status_code == 422, "Expected validation error for malformed payload"


def test_bounty_not_found(client: TestClient) -> None:
    """
    Requesting a non‑existent bounty should return a ``404`` error.
    """
    non_existent_id = 9999999
    response = client.get(f"/bounties/{non_existent_id}")
    _log_response(response)

    assert response.status_code == 404, "Expected 404 for missing bounty"