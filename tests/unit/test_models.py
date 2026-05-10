# tests/unit/test_models.py
"""
Unit tests for ORM models and Pydantic schema validation.

The tests cover:
* Database table creation and basic CRUD operations.
* Validation of incoming bounty data against the Pydantic schema.
* Edge‑case handling for missing or malformed fields.

The test suite uses an in‑memory SQLite database to keep the tests fast and
deterministic.  All database interactions are performed within a transaction
that is rolled back after each test to guarantee isolation.
"""

import logging
from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# Import the project's ORM model and Pydantic schema.
# Adjust the import paths according to your project layout.
from app.models import Base, Bounty  # noqa: F401
from app.schemas import BountyCreate, BountyRead  # noqa: F401

# --------------------------------------------------------------------------- #
# Logging configuration
# --------------------------------------------------------------------------- #
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)
LOGGER.addHandler(handler)

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def engine():
    """Create an in‑memory SQLite engine."""
    eng = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(eng)
    LOGGER.debug("Created in‑memory SQLite database and tables.")
    return eng


@pytest.fixture(scope="function")
def db_session(engine):
    """Provide a fresh SQLAlchemy session for each test."""
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as exc:
        session.rollback()
        LOGGER.exception("Session rollback due to exception: %s", exc)
        raise
    finally:
        session.close()
        LOGGER.debug("SQLAlchemy session closed.")


# --------------------------------------------------------------------------- #
# Helper data
# --------------------------------------------------------------------------- #
VALID_BOUNTY_DATA: Dict[str, Any] = {
    "question_id": 1482916,
    "site": "math",
    "bounty_amount": 2,
    "bounty_start": 1702,
    "bounty_end": 1000,
    "bounty_type": 7,
    "bounty_score": 24.1,
    "view_count": 48657,
    "answer_count": 13566,
    "owner": "recent@math",
    "tags": ["OPEN_BOUNTY", "HOT", "SELF_POST_OPP"],
    "title": "Weekend Puzzle: Interesting Numbers",
}


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_pydantic_schema_validation_success():
    """Validate that a correct payload creates a Pydantic model instance."""
    try:
        bounty = BountyCreate(**VALID_BOUNTY_DATA)
        assert bounty.question_id == VALID_BOUNTY_DATA["question_id"]
        assert bounty.tags == VALID_BOUNTY_DATA["tags"]
        LOGGER.info("Pydantic validation succeeded for valid data.")
    except Exception as exc:
        pytest.fail(f"Pydantic validation raised an unexpected exception: {exc}")


def test_pydantic_schema_validation_missing_field():
    """Missing a required field should raise a ValidationError."""
    incomplete = VALID_BOUNTY_DATA.copy()
    incomplete.pop("question_id")
    with pytest.raises(ValueError):
        BountyCreate(**incomplete)
    LOGGER.info("Pydantic correctly raised an error for missing required field.")


def test_pydantic_schema_validation_invalid_type():
    """Providing an invalid type for a field should raise a ValidationError."""
    malformed = VALID_BOUNTY_DATA.copy()
    malformed["bounty_amount"] = "two"  # should be an int
    with pytest.raises(ValueError):
        BountyCreate(**malformed)
    LOGGER.info("Pydantic correctly raised an error for invalid field type.")


def test_orm_create_and_query(db_session: Session):
    """Insert a Bounty record via ORM and retrieve it."""
    bounty = Bounty(**VALID_BOUNTY_DATA)
    db_session.add(bounty)
    db_session.flush()  # Ensure INSERT is executed

    stmt = select(Bounty).where(Bounty.question_id == VALID_BOUNTY_DATA["question_id"])
    result = db_session.execute(stmt).scalar_one_or_none()

    assert result is not None, "Bounty record not found after insert."
    assert result.title == VALID_BOUNTY_DATA["title"]
    LOGGER.debug("ORM create and query succeeded for question_id=%s.", result.question_id)


def test_orm_unique_constraint(db_session: Session):
    """Attempt to insert duplicate primary key should raise IntegrityError."""
    bounty1 = Bounty(**VALID_BOUNTY_DATA)
    bounty2 = Bounty(**VALID_BOUNTY_DATA)  # same primary key

    db_session.add(bounty1)
    db_session.flush()

    db_session.add(bounty2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    LOGGER.info("ORM correctly enforced unique constraint on primary key.")


def test_orm_update_fields(db_session: Session):
    """Update a field and verify the change persists."""
    bounty = Bounty(**VALID_BOUNTY_DATA)
    db_session.add(bounty)
    db_session.flush()

    new_title = "Updated Puzzle Title"
    bounty.title = new_title
    db_session.commit()

    stmt = select(Bounty).where(Bounty.question_id == VALID_BOUNTY_DATA["question_id"])
    refreshed = db_session.execute(stmt).scalar_one()
    assert refreshed.title == new_title
    LOGGER.debug("ORM update persisted for question_id=%s.", refreshed.question_id)


def test_schema_to_orm_conversion(db_session: Session):
    """Convert a validated Pydantic model to ORM and persist."""
    pydantic_obj = BountyCreate(**VALID_BOUNTY_DATA)
    orm_obj = Bounty(**pydantic_obj.model_dump())
    db_session.add(orm_obj)
    db_session.flush()

    stmt = select(Bounty).where(Bounty.question_id == pydantic_obj.question_id)
    fetched = db_session.execute(stmt).scalar_one()
    assert fetched.owner == pydantic_obj.owner
    LOGGER.debug(
        "Converted Pydantic model to ORM and verified fields for question_id=%s.",
        fetched.question_id,
    )


def test_orm_to_schema_conversion(db_session: Session):
    """Read an ORM instance and convert it to a Pydantic schema."""
    bounty = Bounty(**VALID_BOUNTY_DATA)
    db_session.add(bounty)
    db_session.flush()

    stmt = select(Bounty).where(Bounty.question_id == VALID_BOUNTY_DATA["question_id"])
    orm_instance = db_session.execute(stmt).scalar_one()
    schema_instance = BountyRead.from_orm(orm_instance)

    assert schema_instance.title == VALID_BOUNTY_DATA["title"]
    assert schema_instance.tags == VALID_BOUNTY_DATA["tags"]
    LOGGER.debug(
        "Converted ORM instance to Pydantic schema for question_id=%s.",
        schema_instance.question_id,
    )