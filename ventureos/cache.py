"""Async SQLite KV cache — every external API call goes through this.

Two properties matter:
1. Every cache hit avoids a network call → protects rate limits during demo/rehearsal.
2. Every cache write records `fetched_at` → gives us an audit trail for free.

Cache key convention: f"{tool}:{normalized_params}"
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import aiosqlite

from ventureos.config import CACHE_PATH, CACHE_TTL_HOURS, ensure_cache_dir

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_fetched_at ON cache(fetched_at);
"""

_init_lock = asyncio.Lock()
_initialized = False


async def _ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    async with _init_lock:
        if _initialized:
            return
        ensure_cache_dir()
        async with aiosqlite.connect(str(CACHE_PATH)) as db:
            await db.executescript(_INIT_SQL)
            await db.commit()
        _initialized = True


async def cache_get(key: str, ttl_hours: int = CACHE_TTL_HOURS) -> dict[str, Any] | None:
    """Return cached value if present and not expired, else None."""
    await _ensure_initialized()
    async with aiosqlite.connect(str(CACHE_PATH)) as db:
        async with db.execute(
            "SELECT value, fetched_at FROM cache WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    value_json, fetched_at_str = row
    fetched_at = datetime.fromisoformat(fetched_at_str)
    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600.0
    if age_hours > ttl_hours:
        return None
    return json.loads(value_json)


async def cache_put(key: str, value: dict[str, Any]) -> None:
    """Upsert a cache entry."""
    await _ensure_initialized()
    async with aiosqlite.connect(str(CACHE_PATH)) as db:
        await db.execute(
            "INSERT OR REPLACE INTO cache (key, value, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, default=str), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def call_with_cache(
    key: str,
    fn: Callable[[], Awaitable[dict[str, Any]]],
    ttl_hours: int = CACHE_TTL_HOURS,
) -> dict[str, Any]:
    """Cache-aside pattern: lookup first, call `fn` on miss, store result.

    `fn` MUST return a JSON-serializable dict. Tools should shape their return
    values to be dicts (wrap lists/scalars if needed).
    """
    cached = await cache_get(key, ttl_hours=ttl_hours)
    if cached is not None:
        return cached
    value = await fn()
    await cache_put(key, value)
    return value