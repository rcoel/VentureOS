"""Tavily search + extract tool — used in several roles.

Search:
1. tavily_context(name, company)     — sourcing narrative context
2. tavily_verify(claim, company)     — verification claim cross-check
3. tavily_market_query(query)        — market research
4. tavily_site_search(domain, query) — domain-restricted search (used by
                                        outbound Devpost discovery)

Extract:
5. tavily_extract(urls, query)       — pull structured content from URLs
                                        (used to read Devpost hackathon
                                        pages and pull winner info)

The AsyncTavilyClient's internal httpx session hits SSL races under
concurrency, so ALL calls are serialized via a shared semaphore.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tavily import AsyncTavilyClient

from ventureos.cache import call_with_cache
from ventureos.config import TAVILY_API_KEY
from ventureos.models import EvidenceItem

log = logging.getLogger("ventureos.tools.tavily")

_client: AsyncTavilyClient | None = None
_semaphore = asyncio.Semaphore(1)


def _get_client() -> AsyncTavilyClient | None:
    """Lazy singleton — returns None if no key configured (tool becomes a no-op)."""
    global _client
    if _client is None:
        if not TAVILY_API_KEY:
            return None
        _client = AsyncTavilyClient(api_key=TAVILY_API_KEY)
    return _client


def _trim_result(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": r.get("title"),
        "url": r.get("url"),
        "content": (r.get("content") or "")[:1500],
        "score": r.get("score"),
    }


async def _tavily_search(
    query: str,
    *,
    search_depth: str = "basic",
    max_results: int = 5,
    include_answer: bool = False,
    include_domains: list[str] | None = None,
) -> dict[str, Any]:
    cache_key = (
        f"tavily:{search_depth}:{max_results}:"
        f"{'|'.join(include_domains or [])}:{query.lower()}"
    )

    async def _fetch() -> dict[str, Any]:
        client = _get_client()
        if client is None:
            return {"status": "error", "reason": "TAVILY_API_KEY not set"}
        async with _semaphore:
            try:
                kwargs: dict[str, Any] = {
                    "query": query,
                    "search_depth": search_depth,
                    "max_results": max_results,
                    "include_answer": include_answer,
                }
                if include_domains:
                    kwargs["include_domains"] = include_domains
                resp = await client.search(**kwargs)
            except Exception as e:  # tavily-python raises on network/auth errors
                log.warning("Tavily search failed for %r: %s", query, e)
                return {"status": "error", "reason": str(e)}
        return {
            "status": "ok",
            "query": query,
            "answer": resp.get("answer"),
            "results": [_trim_result(r) for r in resp.get("results", [])],
            "response_time": resp.get("response_time"),
        }

    return await call_with_cache(cache_key, _fetch)


async def _tavily_extract_urls(
    urls: list[str],
    *,
    query: str | None = None,
    extract_depth: str = "basic",
) -> dict[str, Any]:
    """Underlying Tavily Extract call, cached per URL-set."""
    key_urls = "|".join(sorted(urls))
    cache_key = f"tavily_extract:{extract_depth}:{query or ''}:{key_urls[:120]}"

    async def _fetch() -> dict[str, Any]:
        client = _get_client()
        if client is None:
            return {"status": "error", "reason": "TAVILY_API_KEY not set"}
        async with _semaphore:
            try:
                kwargs: dict[str, Any] = {
                    "urls": urls,
                    "extract_depth": extract_depth,
                    "format": "text",
                }
                if query:
                    kwargs["query"] = query
                resp = await client.extract(**kwargs)
            except Exception as e:
                log.warning("Tavily extract failed for %d urls: %s", len(urls), e)
                return {"status": "error", "reason": str(e)}
        # Trim raw_content per result — hackathon pages can be huge
        results = []
        for r in resp.get("results", []):
            results.append(
                {
                    "url": r.get("url"),
                    "raw_content": (r.get("raw_content") or "")[:6000],
                }
            )
        return {
            "status": "ok",
            "results": results,
            "failed_results": resp.get("failed_results", []),
        }

    return await call_with_cache(cache_key, _fetch)


# --------------------------------------------------------------------------- #
# Public tool functions                                                       #
# --------------------------------------------------------------------------- #


async def tavily_context(founder_name: str, company: str) -> list[EvidenceItem]:
    """Sourcing: general founder/company background search."""
    if not founder_name.strip() and not company.strip():
        return []

    query = f'"{founder_name}" {company} founder background'.strip()
    resp = await _tavily_search(query, search_depth="basic", max_results=5)

    status = resp.get("status", "ok")
    results = resp.get("results", []) if status == "ok" else []
    return [
        EvidenceItem(
            founder_name=founder_name,
            source_type="tavily",
            source_url=None,
            raw_content=resp,
            query_used=query,
            status="ok" if results else ("error" if status == "error" else "not_found"),
        )
    ]


async def tavily_verify(
    claim_text: str, company: str, founder_name: str
) -> list[EvidenceItem]:
    """Verification: cross-check a specific claim against the open web."""
    query = f'"{company}" {claim_text}'.strip()
    resp = await _tavily_search(
        query, search_depth="advanced", max_results=5, include_answer=True
    )

    status = resp.get("status", "ok")
    results = resp.get("results", []) if status == "ok" else []
    return [
        EvidenceItem(
            founder_name=founder_name,
            source_type="tavily",
            source_url=None,
            raw_content=resp,
            query_used=f"verify:{query}",
            status="ok" if results else ("error" if status == "error" else "not_found"),
        )
    ]


async def tavily_market_query(
    query: str, founder_name: str, search_depth: str = "advanced"
) -> list[EvidenceItem]:
    """Market research: competitor discovery / market sizing queries."""
    resp = await _tavily_search(
        query, search_depth=search_depth, max_results=8, include_answer=True
    )
    status = resp.get("status", "ok")
    results = resp.get("results", []) if status == "ok" else []
    return [
        EvidenceItem(
            founder_name=founder_name,
            source_type="tavily",
            source_url=None,
            raw_content=resp,
            query_used=f"market:{query}",
            status="ok" if results else ("error" if status == "error" else "not_found"),
        )
    ]


async def tavily_site_search(
    domain: str, query: str, *, max_results: int = 8
) -> dict[str, Any]:
    """Domain-restricted Tavily search — used by the outbound Devpost
    discovery step (no EvidenceItem wrapping, returns raw response dict)."""
    return await _tavily_search(
        query=query,
        search_depth="advanced",
        max_results=max_results,
        include_domains=[domain],
    )


async def tavily_extract(
    urls: list[str], *, query: str | None = None
) -> dict[str, Any]:
    """Extract page contents from a list of URLs — used to pull hackathon
    winner info from Devpost pages found by tavily_site_search."""
    # Tavily caps at 20 URLs per call
    urls = urls[:20]
    if not urls:
        return {"status": "ok", "results": [], "failed_results": []}
    return await _tavily_extract_urls(urls, query=query, extract_depth="basic")