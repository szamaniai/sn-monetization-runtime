# src/app/scheduler.py
import csv
import logging
from asyncio import CancelledError
from typing import List

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

from .config import Settings
from .services.bounty_service import BountyService
from .repositories.bounty_repository import BountyRepository
from .models.bounty import BountyCreate

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Scheduler implementation
# --------------------------------------------------------------------------- #

class SNFeedScheduler:
    """
    APScheduler based background worker that periodically fetches the StackExchange
    “SN” feed, parses the CSV payload, forwards each entry to the
    :class:`BountyService` for validation/transform, and persists the result
    using :class:`BountyRepository`.

    The scheduler is started from the FastAPI application lifecycle events.
    """

    def __init__(
        self,
        settings: Settings,
        bounty_service: BountyService,
        bounty_repo: BountyRepository,
    ) -> None:
        self.settings = settings
        self.bounty_service = bounty_service
        self.bounty_repo = bounty_repo
        self.scheduler = AsyncIOScheduler()
        self._configure_job()

    def _configure_job(self) -> None:
        """Configure the recurring job based on ``settings.poll_interval_seconds``."""
        trigger = IntervalTrigger(seconds=self.settings.poll_interval_seconds)
        self.scheduler.add_job(
            self._run_once,
            trigger,
            name="sn_feed_poll",
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "SN feed job scheduled every %s seconds",
            self.settings.poll_interval_seconds,
        )

    async def _run_once(self) -> None:
        """
        Execute a single polling cycle:
        1. Retrieve the raw CSV feed.
        2. Parse each line into a ``dict``.
        3. Validate/transform via ``BountyService``.
        4. Persist the resulting model with ``BountyRepository``.
        """
        try:
            raw_data = await self._fetch_feed()
            rows = self._parse_csv(raw_data)
            await self._process_rows(rows)
        except CancelledError:
            # Propagate cancellation for graceful shutdown
            raise
        except Exception as exc:  # pragma: no cover – top‑level safety net
            logger.exception("Unexpected error in SN feed job: %s", exc)

    async def _fetch_feed(self) -> str:
        """Download the SN feed using ``httpx.AsyncClient``."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(self.settings.sn_feed_url)
            response.raise_for_status()
            logger.debug("Fetched SN feed (%d bytes)", len(response.text))
            return response.text

    def _parse_csv(self, raw: str) -> List[dict]:
        """
        Convert the raw CSV string into a list of dictionaries.
        The feed is expected to be tab‑delimited with a header line.
        """
        lines = raw.strip().splitlines()
        if not lines:
            logger.warning("SN feed returned empty payload")
            return []

        reader = csv.DictReader(lines, delimiter="\t")
        rows = [row for row in reader]
        logger.debug("Parsed %d SN rows", len(rows))
        return rows

    async def _process_rows(self, rows: List[dict]) -> None:
        """
        For each parsed row, delegate to the service layer and persist the result.
        Errors for individual rows are logged but do not abort the whole batch.
        """
        for row in rows:
            try:
                bounty_data: BountyCreate = self.bounty_service.from_raw(row)
                await self.bounty_repo.create(bounty_data)
                logger.info(
                    "Persisted bounty id=%s title=%s",
                    bounty_data.stackexchange_id,
                    bounty_data.title,
                )
            except Exception as exc:  # pragma: no cover – row‑level safety net
                logger.error(
                    "Failed to process bounty row %s: %s",
                    row.get("stackexchange_id", "unknown"),
                    exc,
                )

    # ----------------------------------------------------------------------- #
    # Public API for FastAPI integration
    # ----------------------------------------------------------------------- #

    def start(self) -> None:
        """Start the APScheduler instance."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("SN feed scheduler started")

    def shutdown(self) -> None:
        """Gracefully shutdown the APScheduler instance."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("SN feed scheduler stopped")


# --------------------------------------------------------------------------- #
# FastAPI lifecycle integration helpers
# --------------------------------------------------------------------------- #

def init_scheduler(app: FastAPI) -> None:
    """
    Attach the scheduler to a FastAPI application.
    The scheduler will start when the app starts and stop on shutdown.
    """
    # Dependency injection – the concrete implementations are created elsewhere
    # and attached to the app's state for reuse.
    async def on_startup() -> None:
        scheduler: SNFeedScheduler = app.state.sn_scheduler  # type: ignore[attr-defined]
        scheduler.start()

    async def on_shutdown() -> None:
        scheduler: SNFeedScheduler = app.state.sn_scheduler  # type: ignore[attr-defined]
        scheduler.shutdown()

    app.add_event_handler("startup", on_startup)
    app.add_event_handler("shutdown", on_shutdown)