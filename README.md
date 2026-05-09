# app/settings.py
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseSettings, Field, PostgresDsn, SQLiteDsn

class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env file."""

    APP_NAME: str = "OpenBountyService"
    ENV: Literal["development", "production", "testing"] = Field(
        default="development", env="ENV"
    )
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    POLL_INTERVAL_SECONDS: int = Field(default=300, env="POLL_INTERVAL_SECONDS")
    FEED_URL: str = Field(
        default="https://api.stackexchange.com/2.3/feeds/sn",
        env="FEED_URL",
    )
    # Database URL – supports SQLite for dev and PostgreSQL for prod
    DATABASE_URL: str = Field(
        default_factory=lambda: (
            f"sqlite+aiosqlite:///{Path.cwd()}/data.db"
            if os.getenv("ENV", "development") != "production"
            else "postgresql+asyncpg://user:password@db:5432/openbounty"
        ),
        env="DATABASE_URL",
    )
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()