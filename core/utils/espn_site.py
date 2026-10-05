"""Fetching JSON from ESPN's public site API (no login needed)."""

import asyncio
import aiohttp
from aiohttp import ClientTimeout
from core.errors import APIFetchError
from datetime import datetime
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Timeouts and retries
ESPN_API_TIMEOUT_SECONDS = 10
ESPN_RETRY_DELAY_SECONDS = 5


async def fetch_json(url: str) -> dict[str, Any]:
    """Fetches a URL from ESPN's site API and returns the parsed JSON.

    Retries once after a timeout. Raises APIFetchError if it still fails.
    """
    try:
        async with aiohttp.ClientSession(
            timeout=ClientTimeout(total=ESPN_API_TIMEOUT_SECONDS)
        ) as session:
            try:
                async with session.get(url) as resp:
                    data = await resp.json()
            except asyncio.TimeoutError:
                logger.warning(
                    "API timeout against ESPN, retrying in 5 seconds. URL=%s", url
                )
                await asyncio.sleep(ESPN_RETRY_DELAY_SECONDS)
                async with session.get(url) as resp:
                    data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        raise APIFetchError(url, e) from e

    return data


def parse_espn_date(datestr: str) -> datetime:
    """Parses an ESPN date like "2025-09-21T18:00Z" into an aware UTC datetime."""
    return datetime.fromisoformat(datestr.replace("Z", "+00:00"))
