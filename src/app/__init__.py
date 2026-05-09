"""
app package initializer.

This module prepares the ``app`` package for import by configuring a
module‑level logger, exposing the public API of the package and providing
a small helper for accessing the application settings.

The package follows the three‑layer architecture described in the project
specification (Data → Service → API) and relies on a ``settings`` module
generated from environment variables.  Importing ``app`` will therefore
initialise logging and make the most common symbols readily available.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------- #
# Logging configuration
# --------------------------------------------------------------------------- #
_LOG_FORMAT: Final = (
    "%(asctime)s %(levelname)s %(name)s %(module)s:%(lineno)d - %(message)s"
)
_LOG_LEVEL: Final = logging.INFO

# Configure a module‑level logger if the root logger has not been configured
# yet.  This is safe to call multiple times and respects any existing
# configuration performed by the application entry‑point.
if not logging.getLogger().handlers:
    logging.basicConfig(format=_LOG_FORMAT, level=_LOG_LEVEL)

logger: Final[logging.Logger] = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Settings handling
# --------------------------------------------------------------------------- #
try:
    # ``settings`` is expected to be a module inside the ``app`` package that
    # provides a Pydantic ``Settings`` class and a ``get_settings`` function.
    from .settings import Settings, get_settings  # type: ignore
except Exception as exc:  # pragma: no cover
    # If the settings module cannot be imported we still want the package to
    # be importable.  The error is logged and a minimal fallback is provided.
    logger.error(
        "Failed to import ``app.settings``: %s. Falling back to default settings.",
        exc,
    )

    class Settings:  # pylint: disable=too-few-public-methods
        """Fallback settings used when the real configuration cannot be loaded."""

        DEBUG: bool = False
        DATABASE_URL: str = "sqlite:///./bounty.db"
        POLL_INTERVAL_SECONDS: int = 300
        STACKEXCHANGE_API_URL: str = (
            "https://api.stackexchange.com/2.3/questions?order=desc&sort=activity"
        )
        # Add any additional defaults required by the rest of the code base.

    def get_settings() -> Settings:  # type: ignore
        """Return a singleton instance of the fallback ``Settings``."""
        return Settings()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
__all__: Final = [
    "logger",
    "Settings",
    "get_settings",
]