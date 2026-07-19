"""Semantic Scholar Graph API tool.

Two functions:
    fetch_author(name)          → h-index, paper count, affiliations, recent papers
    fetch_papers_by_domain(dom) → recent high-citation papers in a research area

Called conditionally by sourcing_node only when intake flags a research founder
(regex hits for PhD/researcher/paper). Skipping S2 for non-researchers saves
~200ms per pipeline run.

API: https://api.semanticscholar.org/graph/v1/
API key is optional (higher rate limits with one, but works without).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ventureos.cache import call_with_cache
from ventureos.config import SEMANTIC_SCHOLAR_API_KEY
from ventureos.models import EvidenceItem
from ventureos.tools.base import http_get_json, is_error, is_not_found

log = logging.getLogger("ventureos.tools.s2")

_API = "https://api.semanticscholar.org/graph/v1"
# Serialize S2 calls — free tier is 100 req/5min; concurrent calls also
# hit SSL context races on some Python/openssl builds.
_semaphore = asyncio.Semaphore(1)


def _headers() -> dict[str, str]:
    h = {"User-Agent": "ventureos/0.1"}
    if SEMANTIC_SCHOLAR_API_KEY:
        h["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    return h


def _trim_paper(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": p.get("paperId"),
        "title": p.get("title"),
        "year": p.get("year"),
        "venue": p.get("venue"),
        "citation_count": p.get("citationCount"),
        "abstract": (p.get("abstract") or "")[:1000],
        "authors": [a.get("name") for a in (p.get("authors") or [])][:6],
        "url": p.get("url"),
    }


async def fetch_author(name: str, founder_name: str) -> list[EvidenceItem]:
    """Search for an author by name, then fetch their profile + recent papers.

    Two-step: /author/search picks the best-matching candidate (by paper count
    as a rough tiebreaker), then /author/{id}/papers pulls recent output.
    """
    cache_key = f"s2_author:{name.lower()}"

    async def _fetch() -> dict[str, Any]:
        async with _semaphore:
            search = await http_get_json(
                f"{_API}/author/search",
                params={
                    "query": name,
                    "limit": 5,
                    "fields": "name,affiliations,homepage,hIndex,paperCount,citationCount",
                },
                headers=_headers(),
            )
        if is_not_found(search) or is_error(search):
            return {"status": "not_found", "name": name}

        candidates = search.get("data") or []
        if not candidates:
            return {"status": "not_found", "name": name}

        # Pick the candidate with the most papers (rough authority tiebreaker)
        best = max(candidates, key=lambda c: c.get("paperCount") or 0)
        author_id = best.get("authorId")
        if not author_id:
            return {"status": "not_found", "name": name}

        async with _semaphore:
            papers = await http_get_json(
                f"{_API}/author/{author_id}/papers",
                params={
                    "limit": 10,
                    "fields": "title,year,venue,citationCount,abstract,authors,url",
                },
                headers=_headers(),
            )
        recent = (papers.get("data") if not is_error(papers) else None) or []

        return {
            "status": "ok",
            "author": {
                "author_id": author_id,
                "name": best.get("name"),
                "affiliations": best.get("affiliations") or [],
                "homepage": best.get("homepage"),
                "h_index": best.get("hIndex"),
                "paper_count": best.get("paperCount"),
                "citation_count": best.get("citationCount"),
                "url": f"https://www.semanticscholar.org/author/{author_id}",
            },
            "recent_papers": [_trim_paper(p) for p in recent],
        }

    raw = await call_with_cache(cache_key, _fetch)
    status = raw.get("status", "ok")
    author_url = raw.get("author", {}).get("url") if status == "ok" else None
    return [
        EvidenceItem(
            founder_name=founder_name,
            source_type="semantic_scholar",
            source_url=author_url,
            raw_content=raw,
            query_used=f"author:{name}",
            status="ok" if status == "ok" else "not_found",
        )
    ]


async def fetch_papers_by_domain(
    domain: str, founder_name: str, limit: int = 5
) -> list[EvidenceItem]:
    """Recent high-citation papers in a research area — informs Market axis.

    Not scoring the founder; giving downstream nodes context on what the
    field looks like right now (competitive/adjacent research work).
    """
    cache_key = f"s2_papers:{domain.lower()}"

    async def _fetch() -> dict[str, Any]:
        async with _semaphore:
            resp = await http_get_json(
                f"{_API}/paper/search",
                params={
                    "query": domain,
                    "limit": limit,
                    "year": "2024-2026",
                    "fields": "title,year,venue,citationCount,abstract,authors,url",
                },
                headers=_headers(),
            )
        if is_not_found(resp) or is_error(resp):
            return {"status": "not_found", "domain": domain}
        papers = resp.get("data") or []
        return {
            "status": "ok" if papers else "not_found",
            "domain": domain,
            "papers": [_trim_paper(p) for p in papers],
        }

    raw = await call_with_cache(cache_key, _fetch)
    status = raw.get("status", "ok")
    return [
        EvidenceItem(
            founder_name=founder_name,
            source_type="semantic_scholar",
            source_url=None,
            raw_content=raw,
            query_used=f"papers:{domain}",
            status="ok" if status == "ok" else "not_found",
        )
    ]