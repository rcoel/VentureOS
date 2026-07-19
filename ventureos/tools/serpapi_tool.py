"""SerpAPI tool — site-restricted Google searches.

Used as the fallback for sources we don't have direct APIs for:
    site:producthunt.com   → launches
    site:linkedin.com/in   → profiles
    site:techcrunch.com    → press
    site:ycombinator.com   → YC company pages
    site:devpost.com       → hackathon winners

We hit the REST endpoint directly (https://serpapi.com/search.json) rather
than the sync `google-search-results` SDK so we can reuse the async
tools/base infrastructure.
"""

from __future__ import annotations

from typing import Any

from ventureos.cache import call_with_cache
from ventureos.config import SERPAPI_API_KEY
from ventureos.models import EvidenceItem
from ventureos.tools.base import http_get_json, is_error

_API = "https://serpapi.com/search.json"


def _trim_organic(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "position": r.get("position"),
        "title": r.get("title"),
        "link": r.get("link"),
        "displayed_link": r.get("displayed_link"),
        "snippet": (r.get("snippet") or "")[:800],
        "date": r.get("date"),
        "source": r.get("source"),
    }


async def _serp_search(query: str, *, num: int = 10) -> dict[str, Any]:
    """Underlying SerpAPI Google search, cached by full query."""
    cache_key = f"serpapi:{num}:{query.lower()}"

    async def _fetch() -> dict[str, Any]:
        if not SERPAPI_API_KEY:
            return {"status": "error", "reason": "SERPAPI_API_KEY not set"}
        resp = await http_get_json(
            _API,
            params={
                "engine": "google",
                "q": query,
                "num": num,
                "api_key": SERPAPI_API_KEY,
                "hl": "en",
            },
        )
        if is_error(resp):
            return {"status": "error", "reason": resp.get("reason")}
        organic = resp.get("organic_results") or []
        return {
            "status": "ok",
            "query": query,
            "search_metadata": {
                "total_results": resp.get("search_information", {}).get("total_results"),
                "engine_url": resp.get("search_metadata", {}).get("google_url"),
            },
            "organic_results": [_trim_organic(r) for r in organic[:num]],
            "answer_box": resp.get("answer_box"),
            "knowledge_graph": {
                "title": (resp.get("knowledge_graph") or {}).get("title"),
                "description": (resp.get("knowledge_graph") or {}).get("description"),
                "website": (resp.get("knowledge_graph") or {}).get("website"),
            }
            if resp.get("knowledge_graph")
            else None,
        }

    return await call_with_cache(cache_key, _fetch)


async def serpapi_site_search(
    site: str, query: str, founder_name: str, num: int = 10
) -> list[EvidenceItem]:
    """Site-restricted Google search: `site:{site} {query}`.

    Always returns exactly one EvidenceItem, empty results included.
    """
    full_query = f"site:{site} {query}".strip()
    resp = await _serp_search(full_query, num=num)
    status = resp.get("status", "ok")
    hits = resp.get("organic_results", []) if status == "ok" else []
    return [
        EvidenceItem(
            founder_name=founder_name,
            source_type="serpapi",
            source_url=None,
            raw_content=resp,
            query_used=full_query,
            status="ok" if hits else ("error" if status == "error" else "not_found"),
        )
    ]


async def serpapi_open_search(
    query: str, founder_name: str, num: int = 10
) -> list[EvidenceItem]:
    """Open (non-site-restricted) SerpAPI query.

    Used by market_research for competitor / market-sizing searches where
    we want the broadest possible index.
    """
    resp = await _serp_search(query, num=num)
    status = resp.get("status", "ok")
    hits = resp.get("organic_results", []) if status == "ok" else []
    return [
        EvidenceItem(
            founder_name=founder_name,
            source_type="serpapi",
            source_url=None,
            raw_content=resp,
            query_used=query,
            status="ok" if hits else ("error" if status == "error" else "not_found"),
        )
    ]