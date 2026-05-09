"""
Utility module providing a resilient HTTP client based on httpx.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Optional, Union

import httpx

logger = logging.getLogger(__name__)

class HttpClientError(RuntimeError):
    """Base exception for HttpClient errors."""


class HttpClient:
    """Thin async wrapper around httpx.AsyncClient with retry and timeout support.

    Example
    -------
    >>> client = HttpClient()
    >>> response = await client.get("https://example.com/api")
    >>> data = response.json()
    """

    def __init__(
        self,
        *,
        timeout: Union[float, httpx.Timeout] = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        client_kwargs: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """
        Parameters
        ----------
        timeout:
            Default timeout for each request (seconds) or a :class:`httpx.Timeout`.
        max_retries:
            Number of retry attempts after the initial request.
        backoff_factor:
            Factor used to compute sleep between retries: ``backoff_factor * (2 ** retry)``.
        client_kwargs:
            Additional keyword arguments passed to :class:`httpx.AsyncClient`.
        """
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._client = httpx.AsyncClient(timeout=self._timeout, **(client_kwargs or {}))

    async def get(
        self,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
    ) -> httpx.Response:
        """
        Perform a GET request with automatic retries.

        Parameters
        ----------
        url:
            Target URL.
        params:
            Query parameters.
        headers:
            Request headers.
        timeout:
            Override the default timeout for this request.

        Returns
        -------
        httpx.Response
            The successful response.

        Raises
        ------
        HttpClientError
            If all retry attempts fail.
        """
        attempt = 0
        while True:
            try:
                response = await self._client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout or self._timeout,
                )
                response.raise_for_status()
                logger.debug("GET %s succeeded (status=%s)", url, response.status_code)
                return response
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                attempt += 1
                if attempt > self._max_retries:
                    logger.error(
                        "GET %s failed after %d attempts: %s", url, attempt - 1, exc
                    )
                    raise HttpClientError(
                        f"GET request to {url} failed after {self._max_retries} retries"
                    ) from exc

                backoff = self._backoff_factor * (2 ** (attempt - 1))
                logger.warning(
                    "GET %s attempt %d/%d failed: %s – retrying in %.2fs",
                    url,
                    attempt,
                    self._max_retries,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


# Convenience singleton for module‑level usage
default_client = HttpClient()


async def get(
    url: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: Optional[Union[float, httpx.Timeout]] = None,
) -> httpx.Response:
    """
    Shortcut that uses the module‑level ``default_client`` to perform a GET request.

    This function mirrors :meth:`HttpClient.get` and is intended for quick one‑off calls.

    Parameters
    ----------
    url:
        Target URL.
    params:
        Query parameters.
    headers:
        Request headers.
    timeout:
        Override the default timeout.

    Returns
    -------
    httpx.Response
        The successful response.

    Raises
    ------
    HttpClientError
        Propagated from :meth:`HttpClient.get`.
    """
    return await default_client.get(
        url, params=params, headers=headers, timeout=timeout
    )


__all__ = ["HttpClient", "HttpClientError", "default_client", "get"]