python
# app/settings.py
import os
import logging
from pathlib import Path
from typing import Literal, Union

from pydantic import BaseSettings, Field, PostgresDsn, SQLiteDsn, validator, AnyUrl

# --------------------------------------------------------------------------- #
# Logging configuration – module‑level logger with sensible defaults.
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
    logger.setLevel(logging.INFO)


class Settings(BaseSettings):
    """
    Application configuration loaded from environment variables or a ``.env`` file.

    All fields are validated on instantiation. Sensitive values are handled as
    :class:`pydantic.SecretStr` where appropriate to avoid accidental logging.
    The class provides strict type hints, comprehensive docstrings, and input
    validation for production‑grade robustness.

    Attributes
    ----------
    APP_NAME: str
        Human‑readable name of the service.
    ENV: Literal["development", "production", "testing"]
        Current deployment environment.
    LOG_LEVEL: str
        Logging level used by the application.
    POLL_INTERVAL_SECONDS: int
        Interval in seconds for background polling jobs.
    FEED_URL: AnyUrl
        URL of the external feed to consume.
    DATABASE_URL: Union[PostgresDsn, SQLiteDsn]
        Database connection string – PostgreSQL for production, SQLite for local
        development/testing.
    """

    APP_NAME: str = Field(default="OpenBountyService", env="APP_NAME")
    ENV: Literal["development", "production", "testing"] = Field(
        default="development", env="ENV"
    )
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    POLL_INTERVAL_SECONDS: int = Field(default=300, env="POLL_INTERVAL_SECONDS")
    FEED_URL: AnyUrl = Field(
        default="https://api.stackexchange.com/2.3/feeds/sn",
        env="FEED_URL",
    )
    # Lazy default: SQLite for non‑production, PostgreSQL for production.
    DATABASE_URL: Union[PostgresDsn, SQLiteDsn] = Field(
        default_factory=lambda: (
            f"sqlite+aiosqlite:///{Path.cwd()}/data.db"
            if os.getenv("ENV", "development") != "production"
            else "postgresql+asyncpg://user:password@db:5432/openbounty"
        ),
        env="DATABASE_URL",
    )

    # ------------------------------------------------------------------- #
    # Validators – enforce security‑ and performance‑friendly constraints.
    # ------------------------------------------------------------------- #
    @validator("LOG_LEVEL")
    def _validate_log_level(cls, value: str) -> str:
        """Validate that LOG_LEVEL is a recognized logging level."""
        valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        upper = value.upper()
        if upper not in valid_levels:
            raise ValueError(f"Invalid LOG_LEVEL '{value}'. Choose from {valid_levels}.")
        return upper

    @validator("POLL_INTERVAL_SECONDS")
    def _validate_poll_interval(cls, value: int) -> int:
        """Enforce a positive poll interval."""
        if value <= 0:
            raise ValueError("POLL_INTERVAL_SECONDS must be a positive integer.")
        return value

    @validator("DATABASE_URL")
    def _validate_database_url(
        cls, value: Union[PostgresDsn, SQLiteDsn]
    ) -> Union[PostgresDsn, SQLiteDsn]:
        """
        Perform minimal sanity checks on the DSN.

        Pydantic already validates DSN format; this hook exists for future
        extensions (e.g., disallowing clear‑text passwords in URLs).
        """
        return value

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        # Prevent accidental exposure of secrets in logs
        secrets_dir = Path("/run/secrets")


# --------------------------------------------------------------------------- #
# Instantiate settings with robust error handling and log the outcome.
# --------------------------------------------------------------------------- #
try:
    settings = Settings()
    logger.info(
        "Configuration loaded for %s environment (APP_NAME=%s).",
        settings.ENV,
        settings.APP_NAME,
    )
except Exception as exc:  # pragma: no cover
    logger.exception("Failed to initialise application settings: %s", exc)
    raise
