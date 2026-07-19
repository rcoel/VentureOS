"""GitHub REST API tool — user profile, recent repos, and event activity.

Returns EvidenceItem list. Empty-result (user not found) is recorded as an
EvidenceItem with status="not_found", never dropped — this is what enables
honest cold-start reweighting downstream.

Endpoints used (API version 2022-11-28):
    GET /users/{username}
    GET /users/{username}/repos?sort=pushed&per_page=10
    GET /users/{username}/events/public?per_page=100
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from ventureos.cache import call_with_cache
from ventureos.config import GITHUB_TOKEN
from ventureos.models import EvidenceItem
from ventureos.tools.base import http_get_json, is_error, is_not_found, unwrap_list

_API = "https://api.github.com"
# Cap concurrent GitHub API calls per pipeline run — prevents SSL context races
# when multiple candidate handles are probed simultaneously.
_semaphore = asyncio.Semaphore(2)


def _headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ventureos/0.1",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def _summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up ~100 events into compact activity metrics."""
    if not events:
        return {"total_events": 0, "push_events": 0, "days_active": 0, "event_types": {}}
    types = Counter(e.get("type", "Unknown") for e in events)
    days = {e["created_at"][:10] for e in events if e.get("created_at")}
    return {
        "total_events": len(events),
        "push_events": types.get("PushEvent", 0),
        "pr_events": types.get("PullRequestEvent", 0),
        "issue_events": types.get("IssuesEvent", 0),
        "days_active": len(days),
        "event_types": dict(types),
        "first_event_at": min(days) if days else None,
        "last_event_at": max(days) if days else None,
    }


def _summarize_repos(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim repo objects to only the fields we care about."""
    trimmed = []
    for r in repos[:10]:
        trimmed.append(
            {
                "name": r.get("name"),
                "full_name": r.get("full_name"),
                "description": r.get("description"),
                "html_url": r.get("html_url"),
                "language": r.get("language"),
                "stars": r.get("stargazers_count", 0),
                "forks": r.get("forks_count", 0),
                "open_issues": r.get("open_issues_count", 0),
                "pushed_at": r.get("pushed_at"),
                "created_at": r.get("created_at"),
                "fork": r.get("fork", False),
                "archived": r.get("archived", False),
            }
        )
    return trimmed


async def fetch_github(username: str, founder_name: str) -> list[EvidenceItem]:
    """Fetch a single candidate GitHub handle.

    Returns exactly one EvidenceItem — either populated (status="ok"),
    empty (status="not_found"), or errored (status="error"). Never [].
    """
    cache_key = f"github:{username}"

    async def _fetch() -> dict[str, Any]:
        async with _semaphore:
            user = await http_get_json(f"{_API}/users/{username}", headers=_headers())
        if is_not_found(user):
            return {"status": "not_found", "username": username}
        if is_error(user):
            return {"status": "error", "username": username, "reason": user.get("reason")}

        # Parallel would be nice but tenacity retries already provide most of the win;
        # sequential keeps error handling simple.
        async with _semaphore:
            repos_resp = await http_get_json(
                f"{_API}/users/{username}/repos",
                params={"sort": "pushed", "per_page": 10, "type": "owner"},
                headers=_headers(),
            )
        async with _semaphore:
            events_resp = await http_get_json(
                f"{_API}/users/{username}/events/public",
                params={"per_page": 100},
                headers=_headers(),
            )
        repos = unwrap_list(repos_resp) or []
        events = unwrap_list(events_resp) or []

        return {
            "status": "ok",
            "profile": {
                "login": user.get("login"),
                "name": user.get("name"),
                "bio": user.get("bio"),
                "company": user.get("company"),
                "location": user.get("location"),
                "blog": user.get("blog"),
                "email": user.get("email"),
                "twitter_username": user.get("twitter_username"),
                "public_repos": user.get("public_repos", 0),
                "followers": user.get("followers", 0),
                "following": user.get("following", 0),
                "created_at": user.get("created_at"),
                "html_url": user.get("html_url"),
            },
            "repos": _summarize_repos(repos),
            "activity": _summarize_events(events),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    raw = await call_with_cache(cache_key, _fetch)

    status = raw.get("status", "ok")
    item = EvidenceItem(
        founder_name=founder_name,
        source_type="github",
        source_url=f"https://github.com/{username}",
        raw_content=raw,
        query_used=username,
        status="ok" if status == "ok" else ("not_found" if status == "not_found" else "error"),
    )
    return [item]