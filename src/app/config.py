# src/app/config.py
"""
Configuration module for the StackExchange bounty microservice.

Provides a single source of truth for application settings using
`pydantic.BaseSettings`. Settings can be loaded from environment variables,
a ``.env`` file, or directly via constructor arguments.

Typical usage
-------------
>>> from app.config import Settings
>>> settings = Settings()
>>> settings.database_url
'postgresql+psycopg2://user:pass@db:5432/bounty'

The module also configures a basic logger that can be imported by other
components.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Literal, Sequence

from pydantic import (
    AnyUrl,
    BaseSettings,
    Field,
    RedisUrl,
    model_validator,
    ValidationError,
)

# --------------------------------------------------------------------------- #
# Logging configuration
# --------------------------------------------------------------------------- #
LOGGER_NAME = "bounty_service"
logger = logging.getLogger(LOGGER_NAME)
if not logger.handlers:
    # Configure a simple console logger only once
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# --------------------------------------------------------------------------- #
# Settings model
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """
    Pydantic settings model for the bounty microservice.

    All fields can be overridden by environment variables using the
    ``BOUNTY_`` prefix (e.g. ``BOUNTY_DATABASE_URL``). A ``.env`` file located
    at the project root is also loaded automatically when the application
    starts.

    Attributes
    ----------
    environment: Literal["development", "testing", "production"]
        Current runtime environment.
    database_url: AnyUrl
        SQLAlchemy database URL (SQLite, PostgreSQL, etc.).
    poll_interval_seconds: int
        Interval in seconds for the APScheduler polling job.
    stackexchange_api_base: AnyUrl
        Base URL of the StackExchange API used to fetch the CSV feed.
    stackexchange_api_key: str | None
        Optional API key for higher rate limits.
    allowed_tags: Sequence[str]
        Tags that are considered when filtering bounties.
    max_concurrent_requests: int
        Maximum number of concurrent HTTP requests the worker may issue.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        Logging level for the application.
    """

    # ------------------------------------------------------------------- #
    # Core configuration
    # ------------------------------------------------------------------- #
    environment: Literal["development", "testing", "production"] = Field(
        default="development",
        description="Runtime environment",
    )
    database_url: AnyUrl = Field(
        default="sqlite:///./bounty.db",
        description="SQLAlchemy database URL",
    )
    poll_interval_seconds: int = Field(
        default=300,
        ge=10,
        description="Polling interval in seconds (minimum 10)",
    )
    # ------------------------------------------------------------------- #
    # External API configuration
    # ------------------------------------------------------------------- #
    stackexchange_api_base: AnyUrl = Field(
        default="https://api.stackexchange.com/2.3",
        description="Base URL for StackExchange API",
    )
    stackexchange_api_key: str | None = Field(
        default=None,
        description="Optional API key for StackExchange",
    )
    # ------------------------------------------------------------------- #
    # Business‑logic configuration
    # ------------------------------------------------------------------- #
    allowed_tags: Sequence[str] = Field(
        default_factory=lambda: ["python", "javascript", "java"],
        description="Tags to include when filtering bounties",
    )
    max_concurrent_requests: int = Field(
        default=5,
        ge=1,
        description="Maximum concurrent HTTP requests",
    )
    # ------------------------------------------------------------------- #
    # Logging configuration
    # ------------------------------------------------------------------- #
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Application log level",
    )

    # ------------------------------------------------------------------- #
    # Pydantic configuration
    # ------------------------------------------------------------------- #
    class Config:
        env_prefix = "BOUNTY_"
        case_sensitive = False
        env_file = ".env"
        env_file_encoding = "utf-8"

    # ------------------------------------------------------------------- #
    # Validators
    # ------------------------------------------------------------------- #
    @model_validator(mode="after")
    def _apply_logging_level(self) -> "Settings":
        """
        Apply the configured log level to the module logger.
        """
        level = getattr(logging, self.log_level.upper(), logging.INFO)
        logger.setLevel(level)
        logger.debug("Log level set to %s", self.log_level)
        return self

    @model_validator(mode="after")
    def _validate_allowed_tags(self) -> "Settings":
        """
        Ensure that at least one allowed tag is defined.
        """
        if not self.allowed_tags:
            raise ValueError("`allowed_tags` must contain at least one tag")
        logger.debug("Allowed tags: %s", self.allowed_tags)
        return self


# --------------------------------------------------------------------------- #
# Helper to load settings safely
# --------------------------------------------------------------------------- #
def load_settings() -> Settings:
    """
    Load the application settings, handling validation errors gracefully.

    Returns
    -------
    Settings
        A fully‑validated settings instance.

    Raises
    ------
    SystemExit
        If settings cannot be validated.
    """
    try:
        settings = Settings()
        logger.info(
            "Settings loaded: env=%s, db=%s",
            settings.environment,
            settings.database_url,
        )
        return settings
    except ValidationError as exc:
        logger.("Configuration validation error: %s", exc)
        sys.exit(1)


# Export a singleton that can be imported elsewhere
settings: Settings = load_settings()