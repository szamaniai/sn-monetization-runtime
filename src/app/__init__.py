python
"""
app package initializer.

This module prepares the ``app`` package for import by configuring a
module‑level logger, exposing the public API of the package and providing
a small helper for accessing the application settings.

The package follows the three‑layer architecture described in the project
specification (Data → Service → API) and relies on a ``settings`` module
generated from environment variables. Importing ``app`` will therefore
initialise logging and make the most common symbols readily available.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final, Callable

# --------------------------------------------------------------------------- #
# Logging configuration
# --------------------------------------------------------------------------- #
_LOG_FORMAT: Final = (
    "%(asctime)s %(levelname)s %(name)s %(module)s:%(lineno)d - %(message)s"
)
_LOG_LEVEL: Final = logging.INFO

# Attach a NullHandler to avoid “No handler found” warnings if the
# application configures logging later.  ``basicConfig`` is only invoked
# when the root logger has no handlers, preserving any existing
# configuration performed by the entry‑point.
if not logging.getLogger().handlers:
    logging.basicConfig(format=_LOG_FORMAT, level=_LOG_LEVEL)

_logger: Final[logging.Logger] = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())

# --------------------------------------------------------------------------- #
# Settings handling
# --------------------------------------------------------------------------- #
def _import_settings() -> tuple[Callable[[], "Settings"], type]:
    """
    Import the real ``Settings`` class and ``get_settings`` factory.

    Returns
    -------
    tuple[Callable[[], Settings], type]
        ``(get_settings, Settings)`` where ``get_settings`` returns a
        singleton ``Settings`` instance.
    """
    try:
        # ``settings`` is expected to be a module inside the ``app`` package
        # that provides a Pydantic ``Settings`` class and a ``get_settings``
        # function.
        from .settings import Settings, get_settings  # type: ignore
        return get_settings, Settings
    except Exception as exc:  # pragma: no cover
        # Log the import failure but continue to provide a safe fallback.
        _logger.error(
            "Failed to import ``app.settings``: %s. Falling back to default settings.",
            exc,
        )

        class Settings:  # pylint: disable=too-few-public-methods
            """
            Minimal fallback configuration used when the real settings module
            cannot be loaded. All attributes are typed and have sensible
            defaults for a development environment.
            """

            DEBUG: bool = False
            DATABASE_URL: str = "sqlite:///./bounty.db"
            POLL_INTERVAL_SECONDS: int = 300
            STACKEXCHANGE_API_URL: str = (
                "https://api.stackexchange.com/2.3/questions?order=desc&sort=activity"
            )

        def get_settings() -> Settings:  # type: ignore
            """
            Return a singleton instance of the fallback ``Settings``. The
            function is deliberately cheap and thread‑safe.
            """
            return Settings()

        return get_settings, Settings


_get_settings, Settings = _import_settings()

# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
__all__: Final[tuple[str, ...]] = (
    "logger",
    "Settings",
    "get_settings",
)

# Export the configured logger under the public name ``logger``.
logger: Final[logging.Logger] = _logger
