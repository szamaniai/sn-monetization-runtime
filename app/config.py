"""app/config.py

Configuration loader for the StackExchange Open Bounty micro‑service.

- Loads environment variables (with optional .env file) using ``python‑dotenv``.
- Provides typed, validated settings via ``pydantic.BaseSettings``.
- Supplies defaults for common parameters.
- Configures a module‑level logger.
- Exposes a ``get_settings`` helper that caches the instantiated settings.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseSettings, Field, PositiveInt, validator, ValidationError

# --------------------------------------------------------------------------- #
# Logging configuration (module‑level, can be overridden by the application)
# --------------------------------------------------------------------------- #
LOGGER_NAME = "stackexchange_bounty_service"
logger = logging.getLogger(LOGGER_NAME)
if not logger.handlers:
    # Prevent duplicate handlers when the module is reloaded
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Load .env file (if present) – this must happen before Settings validation
# --------------------------------------------------------------------------- #
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
if ENV_PATH.is_file():
    load_dotenv(dotenv_path=ENV_PATH)
    logger.debug("Loaded environment variables from %s", ENV_PATH)
else:
    logger.debug("No .env file found at %s", ENV_PATH)

# --------------------------------------------------------------------------- #
# Settings definition
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """Application configuration with validation and sensible defaults."""

    # Database configuration -------------------------------------------------
    DB_DRIVER: Literal["sqlite", "postgresql"] = Field(
        default="sqlite",
        description="Database driver to use. Supported: sqlite, postgresql",
    )
    DB_HOST: str = Field(
        default="localhost",
        description="Hostname of the PostgreSQL server (ignored for SQLite)",
    )
    DB_PORT: PositiveInt = Field(
        default=5432,
        description="Port of the PostgreSQL server (ignored for SQLite)",
    )
    DB_NAME: str = Field(
        default="bounty.db",
        description="SQLite file name or PostgreSQL database name",
    )
    DB_USER: str = Field(
        default="postgres",
        description="PostgreSQL user (ignored for SQLite)",
    )
    DB_PASSWORD: str = Field(
        default="",
        description="PostgreSQL password (ignored for SQLite)",
    )
    DB_URL: str | None = Field(
        default=None,
        description="Full SQLAlchemy URL. If omitted, it is built from the components above.",
    )

    # Radar configuration ----------------------------------------------------
    RADAR_URL: str = Field(
        default="https://api.stackexchange.com/2.3/questions?order=desc&sort=activity&site=math&filter=!-*f(6rc.lF5)",
        description="Endpoint used by the Radar component to fetch open bounty data.",
    )

    # Scheduler configuration ------------------------------------------------
    SCHEDULE_INTERVAL_SECONDS: PositiveInt = Field(
        default=300,
        description="How often (in seconds) the Radar scanner runs.",
    )

    # Miscellaneous -----------------------------------------------------------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Root logging level for the service.",
    )

    # ------------------------------------------------------------------- #
    # Validators
    # ------------------------------------------------------------------- #
    @validator("DB_URL", pre=True, always=True)
    def assemble_db_url(cls, v: str | None, values: dict) -> str:
        """Construct a full SQLAlchemy URL if not provided."""
        if v:
            return v

        driver = values.get("DB_DRIVER")
        name = values.get("DB_NAME")

        if driver == "sqlite":
            # SQLite uses a file path; ensure absolute path for Docker compatibility
            db_path = Path(name).expanduser().resolve()
            url = f"sqlite:///{db_path}"
            logger.debug("Assembled SQLite URL: %s", url)
            return url

        # PostgreSQL
        user = values.get("DB_USER")
        password = values.get("DB_PASSWORD")
        host = values.get("DB_HOST")
        port = values.get("DB_PORT")
        password_part = f":{password}" if password else ""
        url = f"postgresql+psycopg2://{user}{password_part}@{host}:{port}/{name}"
        logger.debug("Assembled PostgreSQL URL: %s", url)
        return url

    @validator("RADAR_URL")
    def validate_radar_url(cls, v: str) -> str:
        """Very lightweight URL validation – ensures scheme and netloc."""
        from urllib.parse import urlparse

        parsed = urlparse(v)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid RADAR_URL: {v}")
        return v

    @validator("LOG_LEVEL")
    def set_logging_level(cls, v: str) -> str:
        """Apply the selected log level to the module logger."""
        logger.setLevel(v)
        logger.debug("Log level set to %s", v)
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        # Prevent pydantic from silently ignoring unknown env vars
        extra = "ignore"


# --------------------------------------------------------------------------- #
# Cached settings instance
# --------------------------------------------------------------------------- #
_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """
    Return a singleton ``Settings`` instance.

    The first call loads and validates the environment; subsequent calls
    return the cached object, guaranteeing a single source of truth.
    """
    global _settings_instance
    if _settings_instance is None:
        try:
            _settings_instance = Settings()
            logger.info("Configuration loaded successfully")
        except ValidationError as exc:
            logger.error("Configuration validation failed: %s", exc)
            raise
    return _settings_instance


# --------------------------------------------------------------------------- #
# Convenience helpers
# --------------------------------------------------------------------------- #
def get_database_url() -> str:
    """
    Shortcut to retrieve the fully‑qualified SQLAlchemy database URL.
    """
    return get_settings().DB_URL


def get_radar_url() -> str:
    """Shortcut to retrieve the Radar endpoint."""
    return get_settings().RADAR_URL


def get_schedule_interval() -> int:
    """Shortcut to retrieve the scheduler interval in seconds."""
    return get_settings().SCHEDULE_INTERVAL_SECONDS


__all__ = [
    "Settings",
    "get_settings",
    "get_database_url",
    "get_radar_url",
    "get_schedule_interval",
    "logger",
]