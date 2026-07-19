"""GitHub handle discovery — SerpAPI Google search + GitHub API verification.

Flow:
1. SerpAPI plain Google query: `"<founder_name>" github account`
2. Parse the top ~5 organic_results for `github.com/{handle}` links.
3. Verify each candidate against GitHub's /users/{handle} endpoint.
   Strict verification — accept ONLY if any of:
      - a founder-name token appears in profile.name (case-insensitive)
      - company appears in profile.bio / .company / .blog
      - handle string similarity to founder name is ≥ 0.6
4. Return only verified handles.

If SerpAPI is unavailable or no candidates verify, fall back to a small set
of name-derived guesses (top-2). Downstream, the extraction identity
guardrail still runs as a second safety net.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any

import httpx

from ventureos.cache import call_with_cache
from ventureos.config import GITHUB_TOKEN, SERPAPI_API_KEY
from ventureos.tools.base import http_get_json, is_error

log = logging.getLogger("ventureos.tools.github_discovery")

_GH_URL_RE = re.compile(
    r"github\.com/([A-Za-z0-9][A-Za-z0-9\-_]*)(?:/|$|[?#])",
    re.IGNORECASE,
)
_RESERVED = {
    "orgs", "features", "topics", "pricing", "about", "settings",
    "explore", "marketplace", "enterprise", "sponsors", "security",
    "login", "signup", "join", "pulls", "issues", "search", "notifications",
    "apps", "readme", "trending", "collections", "customer-stories", "team",
    "codespaces", "copilot", "actions", "packages", "discussions", "advisories",
    "site", "watching", "stars", "assets", "gh", "premium-support",
}


def _handles_from_result(result: dict[str, Any]) -> list[str]:
    """Return every github handle mentioned in a SerpAPI organic result (link
    + snippet + displayed_link)."""
    handles: list[str] = []
    fields = [
        result.get("link") or "",
        result.get("displayed_link") or "",
        result.get("snippet") or "",
    ]
    for f in fields:
        for m in _GH_URL_RE.finditer(f):
            h = m.group(1).lower()
            if h in _RESERVED or len(h) < 2:
                continue
            if h not in handles:
                handles.append(h)
    return handles


async def _serpapi_search(query: str, num: int = 5) -> dict[str, Any]:
    """Underlying SerpAPI Google search, cached."""
    cache_key = f"serpapi_ghsearch:{num}:{query.lower()}"

    async def _fetch() -> dict[str, Any]:
        if not SERPAPI_API_KEY:
            return {"status": "error", "reason": "SERPAPI_API_KEY not set"}
        resp = await http_get_json(
            "https://serpapi.com/search.json",
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
        return {
            "status": "ok",
            "results": resp.get("organic_results") or [],
        }

    return await call_with_cache(cache_key, _fetch)


async def _fetch_github_user(handle: str) -> dict[str, Any] | None:
    """Fetch a GitHub user profile. Returns None on 404/error."""
    cache_key = f"gh_user_verify:{handle.lower()}"

    async def _fetch() -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ventureos/0.1",
        }
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"https://api.github.com/users/{handle}", headers=headers)
                if r.status_code == 404:
                    return {"status": "not_found"}
                if r.status_code >= 400:
                    return {"status": "error", "code": r.status_code}
                return {"status": "ok", "profile": r.json()}
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    resp = await call_with_cache(cache_key, _fetch)
    if resp.get("status") != "ok":
        return None
    return resp.get("profile")


def _verify_strict(
    profile: dict[str, Any], handle: str, founder_name: str, company: str | None
) -> tuple[bool, str]:
    """Strict verification — at least one signal must strongly link the
    profile to the founder or company. Returns (passed, reason)."""
    name_tokens = [t for t in re.split(r"\s+", (founder_name or "").lower()) if len(t) >= 3]
    profile_name = (profile.get("name") or "").lower()
    profile_bio = (profile.get("bio") or "").lower()
    profile_company = (profile.get("company") or "").lower()
    profile_blog = (profile.get("blog") or "").lower()

    # Name-token in profile.name
    for tok in name_tokens:
        if tok in profile_name:
            return True, f"name-token '{tok}' matches profile.name '{profile.get('name')}'"

    # Company in bio/company/blog
    if company:
        c_low = company.lower()
        if c_low in profile_bio:
            return True, f"company '{company}' in bio"
        if c_low in profile_company:
            return True, f"company '{company}' in profile.company"
        if c_low in profile_blog:
            return True, f"company '{company}' in blog URL"

    # Handle-name similarity
    if founder_name:
        norm_name = re.sub(r"[^a-z0-9]", "", founder_name.lower())
        norm_handle = re.sub(r"[^a-z0-9]", "", handle.lower())
        ratio = difflib.SequenceMatcher(None, norm_name, norm_handle).ratio()
        if ratio >= 0.6:
            return True, f"handle '{handle}' ~= name (similarity={ratio:.2f})"

    return False, (
        f"no name-token match; no company mention; handle similarity < 0.6"
    )


def _fallback_from_name(name: str) -> list[str]:
    parts = [p for p in re.split(r"\s+", (name or "").strip().lower()) if p]
    if not parts:
        return []
    if len(parts) == 1:
        h = parts[0]
        return [h] if len(h) >= 3 else []
    first, *rest = parts
    last = rest[-1] if rest else ""
    out: list[str] = []
    if last:
        out.append(f"{first}{last}")
        out.append(f"{first}-{last}")
    return [c for c in out if len(c) >= 3][:2]


async def discover_github_handles(
    founder_name: str,
    company: str | None = None,
    max_handles: int = 3,
) -> tuple[list[str], str]:
    """Search SerpAPI + verify against GitHub. Returns (handles, source_note)."""
    name = (founder_name or "").strip()
    if not name:
        return [], "empty founder_name → no handles"

    # HN-style handles: single-token names ≤ 5 chars — use as-is
    tokens = name.split()
    if len(tokens) == 1 and len(name) <= 5:
        return [name.lower()], "founder_name looks like a handle; using as-is"

    query = f'"{name}" github account'
    if company:
        query = f'"{name}" {company} github'

    resp = await _serpapi_search(query, num=5)
    if resp.get("status") != "ok":
        fb = _fallback_from_name(name)
        return fb, f"SerpAPI unavailable ({resp.get('reason')}) → fallback: {fb}"

    # Collect candidate handles from top 5 organic results
    candidates: list[str] = []
    for r in resp.get("results", [])[:5]:
        for h in _handles_from_result(r):
            if h not in candidates:
                candidates.append(h)
        if len(candidates) >= max_handles + 2:  # a few extras to allow verification failures
            break

    if not candidates:
        fb = _fallback_from_name(name)
        return fb, f"SerpAPI returned no github URLs → fallback: {fb}"

    # Verify each via GitHub API
    verified: list[str] = []
    reasons: list[str] = []
    for h in candidates[: max_handles + 2]:
        profile = await _fetch_github_user(h)
        if profile is None:
            reasons.append(f"{h}: profile not found")
            continue
        passed, reason = _verify_strict(profile, h, name, company)
        if passed:
            verified.append(h)
            reasons.append(f"{h}: ✓ {reason}")
        else:
            reasons.append(f"{h}: ✗ {reason}")
        if len(verified) >= max_handles:
            break

    if not verified:
        return [], (
            f"SerpAPI: {len(candidates)} candidate(s) → 0 verified. "
            + " | ".join(reasons[:3])
        )

    return verified, (
        f"SerpAPI: {len(candidates)} candidate(s) → {len(verified)} verified. "
        + " | ".join(r for r in reasons if r.split(': ')[1].startswith('✓'))
    )