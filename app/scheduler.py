# app/scheduler.py
"""
APScheduler background job that periodically polls the StackExchange Open Bounty feed
via the radar client, normalises the data, stores it in the relational database and
publishes the new bounty entries to a Redis message queue.

The module is deliberately side‑effect free – it only defines the scheduler and
the job function.  The scheduler is started by importing ``start_scheduler`` from
the application entry‑point (e.g. ``app/main.py``).

Typical usage
-------------
    from app.scheduler import start_scheduler
    start_scheduler()
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List

import redis
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, ValidationError, validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# Local imports – these modules must exist in the package.
from app.db import SessionLocal, engine
from app.models import Bounty  # SQLAlchemy model
from app.radar_client import fetch_open_bounties  # Callable returning List[Dict[str, Any]]

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_CHANNEL = os.getenv("REDIS_CHANNEL", "bounty_updates")

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)

# --------------------------------------------------------------------------- #
# Pydantic validation model
# --------------------------------------------------------------------------- #
class BountyPayload(BaseModel):
    """Schema for a single bounty entry returned by the radar client."""

    bounty_id: int
    site: str
    score: int
    view_count: int
    answer_count: int
    comment_count: int
    bounty_amount: float
    creation_date: datetime
    last_activity_date: datetime
    owner: str
    tags: List[str] = []
    title: str
    link: str

    @validator("creation_date", "last_activity_date", pre=True)
    def parse_timestamp(cls, v: Any) -> datetime:
        """Convert Unix timestamps or ISO strings to ``datetime``."""
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v)
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v)
            except ValueError as exc:
                raise ValueError(f"Invalid datetime string: {v}") from exc
        if isinstance(v, datetime):
            return v
        raise TypeError(f"Unsupported datetime type: {type(v)}")

# --------------------------------------------------------------------------- #
# Redis client (singleton)
# --------------------------------------------------------------------------- #
_redis_client: redis.Redis | None = None


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


# --------------------------------------------------------------------------- #
# Core job implementation
# --------------------------------------------------------------------------- #
def _store_bounty(session: Session, payload: BountyPayload) -> Bounty:
    """
    Insert a new bounty or update an existing one.

    Args:
        session: SQLAlchemy session.
        payload: Validated bounty payload.

    Returns:
        The persisted ``Bounty`` ORM instance.
    """
    bounty = (
        session.query(Bounty)
        .filter(Bounty.bounty_id == payload.bounty_id, Bounty.site == payload.site)
        .one_or_none()
    )
    if bounty is None:
        bounty = Bounty(
            bounty_id=payload.bounty_id,
            site=payload.site,
            score=payload.score,
            view_count=payload.view_count,
            answer_count=payload.answer_count,
            comment_count=payload.comment_count,
            bounty_amount=payload.bounty_amount,
            creation_date=payload.creation_date,
            last_activity_date=payload.last_activity_date,
            owner=payload.owner,
            tags=",".join(payload.tags),
            title=payload.title,
            link=payload.link,
        )
        session.add(bounty)
        logger.debug("Created new bounty %s on %s", payload.bounty_id, payload.site)
    else:
        # Update mutable fields
        bounty.score = payload.score
        bounty.view_count = payload.view_count
        bounty.answer_count = payload.answer_count
        bounty.comment_count = payload.comment_count
        bounty.bounty_amount = payload.bounty_amount
        bounty.last_activity_date = payload.last_activity_date
        bounty.owner = payload.owner
        bounty.tags = ",".join(payload.tags)
        bounty.title = payload.title
        bounty.link = payload.link
        logger.debug("Updated existing bounty %s on %s", payload.bounty_id, payload.site)
    return bounty


def _publish_to_redis(bounty: Bounty) -> None:
    """
    Publish a JSON representation of the bounty to the configured Redis channel.

    Args:
        bounty: The ORM instance that has just been persisted.
    """
    client = _get_redis_client()
    payload = {
        "bounty_id": bounty.bounty_id,
        "site": bounty.site,
        "score": bounty.score,
        "view_count": bounty.view_count,
        "answer_count": bounty.answer_count,
        "comment_count": bounty.comment_count,
        "bounty_amount": bounty.bounty_amount,
        "creation_date": bounty.creation_date.isoformat(),
        "last_activity_date": bounty.last_activity_date.isoformat(),
        "owner": bounty.owner,
        "tags": bounty.tags.split(",") if bounty.tags else [],
        "title": bounty.title,
        "link": bounty.link,
    }
    client.publish(REDIS_CHANNEL, json.dumps(payload))
    logger.debug("Published bounty %s to Redis channel %s", bounty.bounty_id, REDIS_CHANNEL)


def radar_job() -> None:
    """
    APScheduler job that fetches the latest open bounties, validates them,
    stores them in the database and pushes notifications to Redis.

    The function is deliberately defensive – any exception is caught and logged
    so the scheduler can continue running on the next interval.
    """
    logger.info("Radar job started")
    try:
        raw_bounties: List[Dict[str, Any]] = fetch_open_bounties()
        logger.debug("Fetched %d raw bounty entries", len(raw_bounties))
    except Exception as exc:
        logger.error("Failed to fetch bounties: %s", exc, exc_info=True)
        return

    session = SessionLocal()
    try:
        for raw in raw_bounties:
            try:
                payload = BountyPayload(**raw)
            except ValidationError as ve:
                logger.warning(
                    "Skipping invalid bounty payload %s: %s", raw.get("bounty_id"), ve
                )
                continue

            try:
                bounty = _store_bounty(session, payload)
                session.commit()
                _publish_to_redis(bounty)
            except SQLAlchemyError as db_err:
                session.rollback()
                logger.error(
                    "Database error for bounty %s: %s", payload.bounty_id, db_err, exc_info=True
                )
            except Exception as exc:
                logger.error(
                    "Unexpected error processing bounty %s: %s",
                    payload.bounty_id,
                    exc,
                    exc_info=True,
                )
    finally:
        session.close()
        logger.info("Radar job finished")


# --------------------------------------------------------------------------- #
# Scheduler lifecycle
# --------------------------------------------------------------------------- #
_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    """
    Initialise and start the APScheduler ``BackgroundScheduler`` with the radar job.

    Returns:
        The configured ``BackgroundScheduler`` instance.
    """
    global _scheduler
    if _scheduler is not None:
        logger.warning("Scheduler already running – returning existing instance")
        return _scheduler

    _scheduler = BackgroundScheduler()
    trigger = IntervalTrigger(seconds=POLL_INTERVAL_SECONDS, start_date=datetime.utcnow())
    _scheduler.add_job(
        radar_job,
        trigger=trigger,
        id="radar_job",
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=60,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started – radar job will run every %d seconds",
        POLL_INTERVAL_SECONDS,
    )
    return _scheduler


def shutdown_scheduler() -> None:
    """
    Gracefully shut down the APScheduler instance if it has been started.
    """
    global _scheduler
    if _scheduler is None:
        logger.info("Scheduler not running – nothing to shut down")
        return
    _scheduler.shutdown(wait=False)
    logger.info("Scheduler shut down")
    _scheduler = None


# --------------------------------------------------------------------------- #
# When the module is executed directly (useful for debugging)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    try:
        start_scheduler()
        # Keep the main thread alive while the background scheduler works.
        import time

        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received – exiting")
        shutdown_scheduler()