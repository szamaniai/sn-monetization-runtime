"""
Integration tests for the Bounty micro‑service API.

The tests verify the full REST contract (CRUD) using FastAPI's TestClient.
A temporary SQLite database is created for each test session to ensure
isolation from production data.

Dependencies:
    - fastapi
    - pytest
    - sqlalchemy
    - pydantic
"""

import logging
import uuid
from typing import Dict, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Import the FastAPI application and the DB utilities from the service.
# Adjust the import paths according to the actual project layout.
from app.main import app  # The FastAPI instance
from app.db import Base, get_db  # SQLAlchemy Base and dependency
from app.models import Bounty  # SQLAlchemy model for a bounty
from app.schemas import BountyCreate, BountyUpdate  # Pydantic schemas

# --------------------------------------------------------------------------- #
# Logging configuration
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)
logger.addHandler(handler)

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def test_engine() -> Generator:
    """
    Create an in‑memory SQLite engine for the whole test session.
    """
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture(scope="function")
def db_session(test_engine) -> Generator[Session, None, None]:
    """
    Provide a SQLAlchemy session bound to the test engine.
    """
    SessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def override_get_db(db_session) -> Generator:
    """
    Override the FastAPI dependency that provides a DB session.
    """
    def _get_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def client(override_get_db) -> TestClient:
    """
    Return a TestClient instance that uses the overridden DB dependency.
    """
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Helper data
# --------------------------------------------------------------------------- #
def _sample_bounty_payload() -> Dict:
    """
    Return a deterministic payload that matches the Bounty schema.
    """
    return {
        "sn": 1482916,
        "site": "math",
        "question_id": 1702,
        "bounty_amount": 1000,
        "bounty_id": 7,
        "reputation": 24.1,
        "view_count": 48657,
        "answer_count": 13566,
        "owner": "recent@math",
        "tags": ["OPEN_BOUNTY", "HOT", "SELF_POST_OPP"],
        "title": "Weekend Puzzle: Interesting Numbers",
    }


# --------------------------------------------------------------------------- #
# Test cases
# --------------------------------------------------------------------------- #
def test_create_bounty(client: TestClient) -> None:
    """
    Verify that a bounty can be created via POST /bounties.
    """
    payload = _sample_bounty_payload()
    response = client.post("/bounties", json=payload)
    assert response.status_code == 201, f"Unexpected status: {response.text}"
    data = response.json()
    for key, value in payload.items():
        assert data.get(key) == value, f"Field {key} mismatch"


def test_get_bounty_list(client: TestClient) -> None:
    """
    Verify that GET /bounties returns a list containing the previously created bounty.
    """
    # Ensure at least one bounty exists
    client.post("/bounties", json=_sample_bounty_payload())

    response = client.get("/bounties")
    assert response.status_code == 200, f"Unexpected status: {response.text}"
    data = response.json()
    assert isinstance(data, list), "Response is not a list"
    assert len(data) > 0, "Bounty list is empty"


def test_get_single_bounty(client: TestClient) -> None:
    """
    Verify that GET /bounties/{id} returns the correct bounty.
    """
    payload = _sample_bounty_payload()
    create_resp = client.post("/bounties", json=payload)
    bounty_id = create_resp.json()["id"]

    response = client.get(f"/bounties/{bounty_id}")
    assert response.status_code == 200, f"Unexpected status: {response.text}"
    data = response.json()
    assert data["id"] == bounty_id, "Returned ID does not match"
    for key, value in payload.items():
        assert data.get(key) == value, f"Field {key} mismatch"


def test_update_bounty(client: TestClient) -> None:
    """
    Verify that a bounty can be updated via PUT /bounties/{id}.
    """
    payload = _sample_bounty_payload()
    create_resp = client.post("/bounties", json=payload)
    bounty_id = create_resp.json()["id"]

    update_payload = {"title": "Updated Puzzle Title", "reputation": 30.5}
    response = client.put(f"/bounties/{bounty_id}", json=update_payload)
    assert response.status_code == 200, f"Unexpected status: {response.text}"
    data = response.json()
    assert data["title"] == update_payload["title"]
    assert data["reputation"] == update_payload["reputation"]


def test_delete_bounty(client: TestClient) -> None:
    """
    Verify that a bounty can be deleted via DELETE /bounties/{id}.
    """
    payload = _sample_bounty_payload()
    create_resp = client.post("/bounties", json=payload)
    bounty_id = create_resp.json()["id"]

    response = client.delete(f"/bounties/{bounty_id}")
    assert response.status_code == 204, f"Unexpected status: {response.text}"

    # Subsequent GET should return 404
    get_resp = client.get(f"/bounties/{bounty_id}")
    assert get_resp.status_code == 404, "Deleted bounty still accessible"


def test_invalid_payload_returns_422(client: TestClient) -> None:
    """
    Verify that sending an invalid payload triggers validation errors (HTTP 422).
    """
    invalid_payload = {"sn": "not-an-int", "site": 123}
    response = client.post("/bounties", json=invalid_payload)
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"