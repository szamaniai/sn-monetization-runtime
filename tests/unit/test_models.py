"""
tests/unit/test_models.py

Unit tests for SQLAlchemy models and Pydantic schema validation.

The tests use an in‑memory SQLite database to avoid side‑effects.
Each test runs in a transaction that is rolled back after the test.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# Import the application's models and schemas.
# Adjust the import paths according to your project layout.
from app.models import Base, Bounty  # type: ignore
from app.schemas import BountySchema  # type: ignore

# --------------------------------------------------------------------------- #
# Logging configuration
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


# --------------------------------------------------------------------------- #
# Pytest fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def engine() -> Generator:
    """Create an in‑memory SQLite engine for the test session."""
    eng = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(eng)
    logger.debug("Created in‑memory SQLite engine and tables.")
    yield eng
    Base.metadata.drop_all(eng)
    logger.debug("Dropped all tables from in‑memory SQLite engine.")


@pytest.fixture(scope="function")
def db_session(engine) -> Generator[Session, None, None]:
    """Provide a transactional SQLAlchemy session per test."""
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session: Session = SessionLocal()
    trans = session.begin()
    logger.debug("Started new DB transaction for test.")
    try:
        yield session
        trans.commit()
        logger.debug("Committed DB transaction for test.")
    except Exception:
        trans.rollback()
        logger.exception("Rolled back DB transaction due to exception.")
        raise
    finally:
        session.close()
        logger.debug("Closed DB session.")


# --------------------------------------------------------------------------- #
# Helper data
# --------------------------------------------------------------------------- #
VALID_BOUNTY_DATA = {
    "site": "math",
    "bounty_id": 1482916,
    "question_id": 2,
    "score": 1702,
    "view_count": 1000,
    "answer_count": 7,
    "average_rating": 24.1,
    "up_votes": 48657,
    "down_votes": 13566,
    "owner": "recent@math",
    "tags": ["OPEN_BOUNTY", "HOT", "SELF_POST_OPP"],
    "title": "Weekend Puzzle: Interesting Numbers",
    "created_at": datetime(2026, 5, 9, 12, 0, 0),
}


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_create_bounty_model(db_session: Session) -> None:
    """Persist a Bounty instance and verify fields."""
    bounty = Bounty(**VALID_BOUNTY_DATA)
    db_session.add(bounty)
    db_session.flush()  # Assign PK without committing
    logger.debug("Inserted Bounty with id=%s", bounty.id)

    stmt = select(Bounty).where(Bounty.id == bounty.id)
    result = db_session.execute(stmt).scalar_one()
    assert result.site == VALID_BOUNTY_DATA["site"]
    assert result.bounty_id == VALID_BOUNTY_DATA["bounty_id"]
    assert result.owner == VALID_BOUNTY_DATA["owner"]
    assert result.title == VALID_BOUNTY_DATA["title"]
    assert result.tags == VALID_BOUNTY_DATA["tags"]


def test_bounty_unique_constraint(db_session: Session) -> None:
    """Two rows with the same (site, bounty_id) should raise an error."""
    first = Bounty(**VALID_BOUNTY_DATA)
    db_session.add(first)
    db_session.flush()
    logger.debug("Inserted first Bounty (id=%s)", first.id)

    duplicate = Bounty(**VALID_BOUNTY_DATA)
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()
    logger.debug("IntegrityError raised as expected for duplicate Bounty.")


def test_bounty_schema_validation_success() -> None:
    """A valid payload should be accepted by the Pydantic schema."""
    schema = BountySchema(**VALID_BOUNTY_DATA)
    assert schema.site == VALID_BOUNTY_DATA["site"]
    assert schema.bounty_id == VALID_BOUNTY_DATA["bounty_id"]
    assert schema.owner == VALID_BOUNTY_DATA["owner"]
    assert schema.title == VALID_BOUNTY_DATA["title"]
    assert schema.tags == VALID_BOUNTY_DATA["tags"]


def test_bounty_schema_missing_required_field() -> None:
    """Omitting a required field must raise a validation error."""
    incomplete = VALID_BOUNTY_DATA.copy()
    incomplete.pop("site")
    with pytest.raises(ValueError) as excinfo:
        BountySchema(**incomplete)
    assert "field required" in str(excinfo.value).lower()


def test_bounty_schema_invalid_type() -> None:
    """Providing an invalid type for a field must raise a validation error."""
    malformed = VALID_BOUNTY_DATA.copy()
    malformed["average_rating"] = "not-a-float"
    with pytest.raises(ValueError) as excinfo:
        BountySchema(**malformed)
    assert "average_rating" in str(excinfo.value).lower()


def test_bounty_model_to_schema(db_session: Session) -> None:
    """Round‑trip: model → dict → schema."""
    bounty = Bounty(**VALID_BOUNTY_DATA)
    db_session.add(bounty)
    db_session.flush()
    logger.debug("Persisted Bounty for round‑trip test (id=%s)", bounty.id)

    # Convert SQLAlchemy model to dict, handling ORM relationships if any.
    model_dict = {
        "site": bounty.site,
        "bounty_id": bounty.bounty_id,
        "question_id": bounty.question_id,
        "score": bounty.score,
        "view_count": bounty.view_count,
        "answer_count": bounty.answer_count,
        "average_rating": bounty.average_rating,
        "up_votes": bounty.up_votes,
        "down_votes": bounty.down_votes,
        "owner": bounty.owner,
        "tags": bounty.tags,
        "title": bounty.title,
        "created_at": bounty.created_at,
    }

    schema = BountySchema(**model_dict)
    assert schema.dict() == model_dict
    logger.debug("Successfully round‑tripped Bounty model to Pydantic schema.")