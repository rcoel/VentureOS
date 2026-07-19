"""Thin async OpenAI wrapper — the ONLY place we call OpenAI from.

Uses structured outputs via `chat.completions.parse` with Pydantic response formats.
Every call is cached by hash(system + user + schema.__name__ + model).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from ventureos.cache import call_with_cache
from ventureos.config import (
    OPENAI_API_KEY,
    OPENAI_MODEL_FAST,
    OPENAI_MODEL_SMART,
)

T = TypeVar("T", bound=BaseModel)

# Single shared async client
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:24]


async def openai_json(
    system: str,
    user: str | dict[str, Any],
    schema: type[T],
    model: str | None = None,
    temperature: float = 0.2,
) -> T:
    """Call OpenAI with structured outputs, cached.

    Parameters
    ----------
    system : str
        System prompt (usually loaded from ventureos/prompts/*.md).
    user : str | dict
        User message. Dicts are JSON-serialized before sending.
    schema : type[BaseModel]
        Pydantic model used both as `response_format` and for parsing.
    model : str, optional
        Override the default model (defaults to gpt-4o-mini for fast tasks).
    temperature : float
        Sampling temperature. Default 0.2 for structured extraction stability.
    """
    model = model or OPENAI_MODEL_FAST
    payload = user if isinstance(user, str) else json.dumps(user, default=str, sort_keys=True)

    cache_key = f"llm:{model}:{schema.__name__}:{_hash(system, payload, str(temperature))}"

    async def _call() -> dict[str, Any]:
        client = _get_client()
        completion = await client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": payload},
            ],
            response_format=schema,
            temperature=temperature,
        )
        message = completion.choices[0].message
        if message.refusal:
            raise RuntimeError(f"OpenAI refused structured output: {message.refusal}")
        if message.parsed is None:
            raise RuntimeError("OpenAI returned no parsed content.")
        # Store as dict; on cache hit we rehydrate via schema(**cached)
        return message.parsed.model_dump()

    data = await call_with_cache(cache_key, _call)
    return schema(**data)


def fast_model() -> str:
    return OPENAI_MODEL_FAST


def smart_model() -> str:
    return OPENAI_MODEL_SMART