"""Shared HTTP + retry infrastructure for all tools.

Every tool builds on `http_get_json` which provides:
- httpx.AsyncClient with 15s timeout
- tenacity retry (3 attempts, exponential backoff) on network/5xx
- Never raises up the call stack: caller receives dict on success, or a dict
  with {"error": ..., "status": ...} on final failure.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ventureos.config import HTTP_RETRIES, HTTP_TIMEOUT_SECONDS

log = logging.getLogger("ventureos.tools")


class TransientHTTPError(Exception):
    """Retryable HTTP error (5xx, timeout, connection reset)."""


async def http_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """GET a URL and return parsed JSON. Never raises to caller.

    Returns:
        - Parsed JSON dict on 2xx
        - {"__status__": "not_found", "url": url} on 404
        - {"__status__": "error", "url": url, "reason": ...} on final failure
    """
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(HTTP_RETRIES),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
            retry=retry_if_exception_type((TransientHTTPError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    r = await client.get(url, params=params, headers=headers)
                    if r.status_code == 404:
                        return {"__status__": "not_found", "url": url}
                    if r.status_code >= 500:
                        raise TransientHTTPError(f"{r.status_code} on {url}")
                    if r.status_code >= 400:
                        # 4xx non-404 → permanent failure, don't retry
                        return {
                            "__status__": "error",
                            "url": url,
                            "reason": f"HTTP {r.status_code}: {r.text[:200]}",
                        }
                    data = r.json()
                    # Wrap non-dict responses (e.g. GitHub returns lists)
                    if isinstance(data, list):
                        return {"__list__": data}
                    return data
    except RetryError as e:
        log.warning("Retries exhausted for %s: %s", url, e)
        return {"__status__": "error", "url": url, "reason": f"retries exhausted: {e}"}
    except Exception as e:
        log.warning("Unexpected error fetching %s: %s", url, e)
        return {"__status__": "error", "url": url, "reason": str(e)}
    # Should not reach here
    return {"__status__": "error", "url": url, "reason": "unknown"}


def unwrap_list(response: dict[str, Any]) -> list[Any] | None:
    """If the response wraps a list (from http_get_json), return the list. Else None."""
    if isinstance(response, dict) and "__list__" in response:
        return response["__list__"]
    return None


def is_not_found(response: dict[str, Any]) -> bool:
    return response.get("__status__") == "not_found"


def is_error(response: dict[str, Any]) -> bool:
    return response.get("__status__") == "error"