# app/dependencies.py
"""
FastAPI dependency injection helpers.

Provides:
- ``get_db`` – a generator yielding a SQLAlchemy ``Session`` (sync) or
  ``AsyncSession`` (async) depending on the configured engine.
- ``get_redis`` – a generator yielding a ``redis.asyncio.Redis`` client.

Both helpers ensure proper resource cleanup and surface connection errors
as HTTPException with a clear status code.
"""

import os
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import Generator, AsyncGenerator

from fastapi import Depends, HTTPException, status
from pydantic import BaseSettings, Field, PostgresDsn, RedisDsn
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, AsyncSession
import redis.asyncio as redis

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
    logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # Database
    DATABASE_URL: PostgresDsn | str = Field(
        default="sqlite:///./app.db",
        description="SQLAlchemy database URL (SQLite or PostgreSQL).",
    )
    # Async flag – if True an async engine/session will be used
    DATABASE_ASYNC: bool = Field(
        default=False,
        description="Use async SQLAlchemy engine.",
    )

    # Redis
    REDIS_URL: RedisDsn = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )
    REDIS_MAX_CONNECTIONS: int = Field(
        default=10,
        description="Maximum number of simultaneous Redis connections.",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


# --------------------------------------------------------------------------- #
# Database engine / session creation
# --------------------------------------------------------------------------- #
def _create_sync_engine() -> Engine:
    """Create a synchronous SQLAlchemy engine."""
    try:
        engine = create_engine(
            settings.DATABASE_URL,
            echo=False,
            future=True,
            pool_pre_ping=True,
        )
        logger.info("Synchronous DB engine created.")
        return engine
    except Exception as exc:
        logger.exception("Failed to create sync DB engine.")
        raise RuntimeError("Database engine initialization error.") from exc


def _create_async_engine() -> AsyncEngine:
    """Create an asynchronous SQLAlchemy engine."""
    try:
        # Convert a regular URL to async scheme if needed
        url = str(settings.DATABASE_URL)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://")
        elif url.startswith("sqlite://"):
            # SQLite async driver
            url = url.replace("sqlite://", "sqlite+aiosqlite://")
        engine = create_async_engine(
            url,
            echo=False,
            future=True,
            pool_pre_ping=True,
        )
        logger.info("Asynchronous DB engine created.")
        return engine
    except Exception as exc:
        logger.exception("Failed to create async DB engine.")
        raise RuntimeError("Async database engine initialization error.") from exc


# Engine singletons
_sync_engine = _create_sync_engine() if not settings.DATABASE_ASYNC else None
_async_engine = _create_async_engine() if settings.DATABASE_ASYNC else None

# Session factories
_sync_session_factory = sessionmaker(
    bind=_sync_engine,
    autocommit=False,
    autoflush=False,
    class_=Session,
    future=True,
) if _sync_engine else None

_async_session_factory = sessionmaker(
    bind=_async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
    future=True,
) if _async_engine else None


# --------------------------------------------------------------------------- #
# Dependency: DB Session
# --------------------------------------------------------------------------- #
@contextmanager
def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a synchronous SQLAlchemy ``Session``.

    Example:
        >>> @app.get("/items")
        ... def read_items(db: Session = Depends(get_db)):
        ...     ...

    The session is automatically closed after the request finishes.
    """
    if _sync_session_factory is None:
        logger.error("Sync DB session factory not initialised.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database session unavailable.",
        )
    db: Session = _sync_session_factory()
    try:
        logger.debug("DB session opened.")
        yield db
    except Exception as exc:
        logger.exception("Exception during DB request handling.")
        raise
    finally:
        db.close()
        logger.debug("DB session closed.")


# --------------------------------------------------------------------------- #
# Dependency: Async DB Session
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an asynchronous SQLAlchemy ``AsyncSession``.

    Use this dependency when the endpoint is defined with ``async def``.
    """
    if _async_session_factory is None:
        logger.error("Async DB session factory not initialised.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Async database session unavailable.",
        )
    async with _async_session_factory() as session:
        try:
            logger.debug("Async DB session opened.")
            yield session
        except Exception as exc:
            logger.exception("Exception during async DB request handling.")
            raise
        finally:
            logger.debug("Async DB session closed.")


# --------------------------------------------------------------------------- #
# Redis client
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def get_redis() -> AsyncGenerator[redis.Redis, None]:
    """
    FastAPI dependency that yields a ``redis.asyncio.Redis`` client.

    The client is created per‑request and closed after use to release
    underlying connections back to the pool.
    """
    try:
        client = redis.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            decode_responses=True,
        )
        logger.debug("Redis client created.")
        yield client
    except Exception as exc:
        logger.exception("Failed to create Redis client.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Redis connection error.",
        ) from exc
    finally:
        await client.close()
        logger.debug("Redis client closed.")