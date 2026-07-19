"""Hacker News tool via Algolia Search API.

Two useful signals:
1. Show HN posts about the company (launch reception, upvotes, comment sentiment)
2. Author-tagged stories/comments (what has this founder posted historically)

API: https://hn.algolia.com/api/v1/search — free, no auth required.
Docs: https://hn.algolia.com/api

Query patterns we use:
    company + Show HN:    search?query={company}&tags=show_hn&hitsPerPage=10
    company mentions:     search?query={company}&tags=story&hitsPerPage=5
"""

from __future__ import annotations

from typing import Any

from ventureos.cache import call_with_cache
from ventureos.models import EvidenceItem
from ventureos.tools.base import http_get_json, is_error

_API = "https://hn.algolia.com/api/v1/search"


def _trim_hit(h: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields useful for extraction; drop bulky Algolia metadata."""
    return {
        "object_id": h.get("objectID"),
        "title": h.get("title"),
        "url": h.get("url"),
        "author": h.get("author"),
        "points": h.get("points"),
        "num_comments": h.get("num_comments"),
        "story_text": (h.get("story_text") or "")[:2000],  # cap length
        "comment_text": (h.get("comment_text") or "")[:1000],
        "created_at": h.get("created_at"),
        "hn_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        if h.get("objectID")
        else None,
    }


async def _search(
    query: str, tags: str, hits_per_page: int = 10
) -> dict[str, Any]:
    """Underlying Algolia call, cached."""
    cache_key = f"hn:{tags}:{query.lower()}"

    async def _fetch() -> dict[str, Any]:
        resp = await http_get_json(
            _API,
            params={"query": query, "tags": tags, "hitsPerPage": hits_per_page},
        )
        if is_error(resp):
            return {"status": "error", "reason": resp.get("reason")}
        hits = resp.get("hits", []) if isinstance(resp, dict) else []
        return {
            "status": "ok",
            "nb_hits": resp.get("nbHits", len(hits)) if isinstance(resp, dict) else len(hits),
            "hits": [_trim_hit(h) for h in hits],
        }

    return await call_with_cache(cache_key, _fetch)


async def fetch_hn(founder_name: str, company: str) -> list[EvidenceItem]:
    """Fetch HN evidence for a company.

    Runs two searches: Show HN filter (highest signal for launch reception),
    and general story mentions (fallback). Both are always recorded — empty
    hits produce an EvidenceItem with status="not_found" so cold-start logic
    downstream can distinguish 'never searched' from 'searched, nothing found'.
    """
    items: list[EvidenceItem] = []
    if not company.strip():
        return items

    # Show HN search
    show_hn = await _search(company, tags="show_hn", hits_per_page=10)
    show_status = show_hn.get("status", "ok")
    show_hits = show_hn.get("hits", []) if show_status == "ok" else []
    items.append(
        EvidenceItem(
            founder_name=founder_name,
            source_type="hn",
            source_url=f"https://hn.algolia.com/?q={company}&type=show_hn",
            raw_content=show_hn,
            query_used=f"show_hn:{company}",
            status="ok" if show_hits else ("error" if show_status == "error" else "not_found"),
        )
    )

    # General story mentions
    stories = await _search(company, tags="story", hits_per_page=5)
    story_status = stories.get("status", "ok")
    story_hits = stories.get("hits", []) if story_status == "ok" else []
    items.append(
        EvidenceItem(
            founder_name=founder_name,
            source_type="hn",
            source_url=f"https://hn.algolia.com/?q={company}&type=story",
            raw_content=stories,
            query_used=f"story:{company}",
            status="ok" if story_hits else ("error" if story_status == "error" else "not_found"),
        )
    )

    return items