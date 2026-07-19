"""Outbound scan — discover candidate founders from public feeds.

Sources:
    1. Hacker News Show HN posts (last N days)
    2. GitHub trending-like signal via search API: recently-created repos
       sorted by stars
    3. Devpost hackathon winners:
         - Tavily search restricted to devpost.com finds hackathon pages
         - Tavily extract pulls page content
         - LLM parses winners into structured DevpostWinner records
       For each winner we emit a candidate with project_name as company,
       primary team member as founder_name. The downstream sourcing node
       then handles finding their GitHub etc.

For each candidate we build a (founder_name, company, application_text)
triple and feed it into the main LangGraph pipeline with is_outbound=True.

Usage:
    uv run python -m scripts.outbound_scan --limit 8 --hours 168
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ventureos.config import GITHUB_TOKEN, LOG_LEVEL, TAVILY_API_KEY
from ventureos.graph import build_graph
from ventureos.llm import fast_model, openai_json
from ventureos.models import (
    DevpostWinnerList,
    HackathonList,
    ProjectRefList,
)
from ventureos.prompts import load_prompt
from ventureos.state import initial_state
from ventureos.tools.base import http_get_json, unwrap_list
from ventureos.tools.tavily_tool import tavily_extract

DEVPOST_INDEX_URL = (
    "https://devpost.com/hackathons?"
    "challenge_type%5B%5D=online&"
    "managed_by_devpost_badge=1&"
    "open_to%5B%5D=public&"
    "order_by=recently-added&"
    "status%5B%5D=ended"
)

log = logging.getLogger("ventureos.outbound")


# --------------------------------------------------------------------------- #
# Candidate discovery — Show HN                                               #
# --------------------------------------------------------------------------- #


async def _discover_show_hn(hours: int, limit: int) -> list[dict[str, Any]]:
    """Pull recent Show HN posts via Algolia."""
    since = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    resp = await http_get_json(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={
            "tags": "show_hn",
            "numericFilters": f"created_at_i>{since}",
            "hitsPerPage": limit,
        },
    )
    hits = resp.get("hits") if isinstance(resp, dict) else []
    candidates = []
    for h in hits or []:
        title = h.get("title") or ""
        if not title.lower().startswith("show hn:"):
            continue
        after = title[len("show hn:"):].strip()
        for sep in [" – ", " - ", ": ", " — ", " -- "]:
            if sep in after:
                after = after.split(sep, 1)[0]
                break
        company = after.strip().rstrip(".").strip()[:80]

        word_count = len(company.split())
        looks_like_sentence = word_count > 4 or any(
            company.lower().startswith(w) for w in ("a ", "an ", "how ", "why ", "what ", "the ")
        )
        if looks_like_sentence:
            url = h.get("url") or ""
            from urllib.parse import urlparse

            host = urlparse(url).netloc.replace("www.", "").split(".")[0] if url else ""
            if host and host not in ("github", "gitlab", "youtube", "youtu"):
                company = host
            else:
                continue
        if not company:
            continue
        candidates.append(
            {
                "source": "hn_show",
                "founder_name": h.get("author") or "",
                "company": company,
                "application_text": (
                    f"Show HN launch: {h.get('title')}\n\n"
                    f"URL: {h.get('url') or ''}\n\n"
                    f"Story: {(h.get('story_text') or '')[:1500]}"
                ),
                "reference_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            }
        )
    return candidates


# --------------------------------------------------------------------------- #
# Candidate discovery — GitHub trending                                       #
# --------------------------------------------------------------------------- #


async def _discover_github_trending(hours: int, limit: int) -> list[dict[str, Any]]:
    """Use GitHub's search API to approximate 'trending recently'."""
    if not GITHUB_TOKEN:
        return []
    since_date = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d")
    resp = await http_get_json(
        "https://api.github.com/search/repositories",
        params={
            "q": f"created:>{since_date} stars:>10",
            "sort": "stars",
            "order": "desc",
            "per_page": limit,
        },
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "User-Agent": "ventureos/0.1",
        },
    )
    if not isinstance(resp, dict):
        return []
    items = resp.get("items") or []
    if not items and "__list__" in resp:
        items = unwrap_list(resp) or []

    candidates = []
    for r in items[:limit]:
        owner = (r.get("owner") or {}).get("login") or ""
        if not owner:
            continue
        company = r.get("name") or ""
        candidates.append(
            {
                "source": "github_trending",
                "founder_name": owner,
                "company": company,
                "application_text": (
                    f"GitHub trending repo: {r.get('full_name')}\n\n"
                    f"Description: {r.get('description') or ''}\n"
                    f"Stars: {r.get('stargazers_count')}\n"
                    f"URL: {r.get('html_url')}\n"
                    f"Language: {r.get('language')}\n"
                    f"Created: {r.get('created_at')}\n"
                ),
                "reference_url": r.get("html_url"),
            }
        )
    return candidates


# --------------------------------------------------------------------------- #
# Candidate discovery — Devpost hackathon winners                             #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Devpost discovery — proper 3-step crawl                                     #
# --------------------------------------------------------------------------- #
#
# The Devpost hackathon lifecycle:
#   1. https://devpost.com/hackathons?status[]=ended&... lists ENDED,
#      Devpost-managed hackathons.
#   2. Each hackathon lives at <slug>.devpost.com (e.g. gitlab.devpost.com).
#   3. Winners are shown on the landing page and/or <slug>.devpost.com/project-gallery
#   4. Individual project pages are at devpost.com/software/<slug> and contain
#      the full project description, team members ("Created by ..."), prize
#      placement, "Built With" tech stack, and Try-it-out links.
#
# So the flow is: index → hackathon → gallery → project pages → winners.
# --------------------------------------------------------------------------- #


async def _fetch_hackathons(limit: int) -> list[dict[str, Any]]:
    """Step 1: extract the Devpost hackathons index page and parse ended hackathons."""
    resp = await tavily_extract(
        [DEVPOST_INDEX_URL],
        query="ended hackathons managed by devpost recently added",
    )
    if resp.get("status") != "ok" or not resp.get("results"):
        log.warning("Devpost index extract failed: %s", resp.get("reason"))
        return []

    page = resp["results"][0]
    raw_content = page.get("raw_content", "") or ""
    if len(raw_content) < 200:
        return []

    try:
        parsed = await openai_json(
            system=load_prompt("devpost_hackathons"),
            user={"url": DEVPOST_INDEX_URL, "content": raw_content},
            schema=HackathonList,
            model=fast_model(),
        )
    except Exception as e:
        log.warning("Devpost index LLM parse failed: %s", e)
        return []

    hackathons: list[dict[str, Any]] = []
    seen_urls = set()
    for h in parsed.hackathons:
        # Only ended (or unknown) — we filtered the URL by status[]=ended
        # so most entries should be ended; keep unknowns since the LLM
        # may not always infer status reliably.
        if h.status not in ("ended", "unknown"):
            continue
        url = h.url.rstrip("/")
        if url in seen_urls or "devpost.com" not in url:
            continue
        seen_urls.add(url)
        hackathons.append({"name": h.name, "url": url, "status": h.status})
        if len(hackathons) >= limit:
            break
    log.info("Devpost index: found %d ended hackathons.", len(hackathons))
    return hackathons


async def _fetch_gallery_project_urls(
    hackathons: list[dict[str, Any]], per_hackathon: int
) -> list[dict[str, Any]]:
    """Step 2: for each hackathon, extract its landing + gallery pages to find
    winning project URLs. Returns list of {project_name, project_url, prize,
    hackathon_name}."""
    if not hackathons:
        return []

    # Build the URL batch — for each hackathon, try both the landing page
    # AND the /project-gallery page. Tavily Extract accepts up to 20 URLs.
    urls: list[str] = []
    url_to_hackathon: dict[str, dict[str, Any]] = {}
    for hack in hackathons:
        base = hack["url"]
        landing = base
        gallery = f"{base}/project-gallery"
        urls.append(landing)
        urls.append(gallery)
        url_to_hackathon[landing] = hack
        url_to_hackathon[gallery] = hack
    urls = urls[:20]

    resp = await tavily_extract(
        urls, query="winners grand prize first place best category winning projects"
    )
    if resp.get("status") != "ok":
        log.warning("Devpost gallery extract failed: %s", resp.get("reason"))
        return []

    system = load_prompt("devpost_gallery")
    projects: list[dict[str, Any]] = []
    seen = set()

    for page in resp.get("results", []):
        page_url = page.get("url", "")
        raw_content = page.get("raw_content", "") or ""
        if len(raw_content) < 200:
            continue
        hack = url_to_hackathon.get(page_url.rstrip("/")) or url_to_hackathon.get(page_url)
        hackathon_name = hack["name"] if hack else None

        try:
            parsed = await openai_json(
                system=system,
                user={"url": page_url, "content": raw_content},
                schema=ProjectRefList,
                model=fast_model(),
            )
        except Exception as e:
            log.warning("Devpost gallery LLM parse failed for %s: %s", page_url, e)
            continue

        found_here = 0
        for p in parsed.projects:
            purl = p.project_url.strip().rstrip("/")
            if "devpost.com/software/" not in purl:
                continue  # only real project URLs
            if purl in seen:
                continue
            seen.add(purl)
            projects.append(
                {
                    "project_name": p.project_name,
                    "project_url": purl,
                    "prize_or_placement": p.prize_or_placement,
                    "hackathon_name": hackathon_name,
                }
            )
            found_here += 1
            if found_here >= per_hackathon:
                break
    log.info(
        "Devpost gallery: %d winning project URLs across %d hackathons.",
        len(projects), len(hackathons),
    )
    return projects


async def _fetch_project_details(
    projects: list[dict[str, Any]], limit_winners: int
) -> list[dict[str, Any]]:
    """Step 3: extract each project page and parse it into DevpostWinner records.

    Returns pipeline-ready candidate dicts.
    """
    if not projects:
        return []

    # Tavily Extract can batch up to 20 URLs per call. We probably have <20.
    project_urls = [p["project_url"] for p in projects][:20]
    url_to_meta = {p["project_url"]: p for p in projects}

    resp = await tavily_extract(
        project_urls,
        query=(
            "project name team members created by prize winner grand prize "
            "built with description github repository"
        ),
    )
    if resp.get("status") != "ok":
        log.warning("Devpost project extract failed: %s", resp.get("reason"))
        return []

    system = load_prompt("devpost_winners")
    candidates: list[dict[str, Any]] = []
    seen = set()

    for page in resp.get("results", []):
        purl = page.get("url", "").rstrip("/")
        raw_content = page.get("raw_content", "") or ""
        if len(raw_content) < 200:
            continue

        meta = url_to_meta.get(purl) or url_to_meta.get(purl + "/")

        try:
            parsed = await openai_json(
                system=system,
                user={"url": purl, "content": raw_content},
                schema=DevpostWinnerList,
                model=fast_model(),
            )
        except Exception as e:
            log.warning("Devpost project LLM parse failed for %s: %s", purl, e)
            continue

        # Prefer the gallery-derived hackathon name / placement if present
        gallery_hackathon = meta.get("hackathon_name") if meta else None
        gallery_prize = meta.get("prize_or_placement") if meta else None

        for w in parsed.winners:
            if not w.project_name.strip() or not w.founder_name.strip():
                continue
            key = (w.founder_name.strip().lower(), w.project_name.strip().lower())
            if key in seen:
                continue
            seen.add(key)

            hackathon = parsed.hackathon_name or gallery_hackathon or "Devpost hackathon"
            placement = w.prize_or_placement or gallery_prize or "Winner"
            application_text = (
                f"Devpost hackathon winner: {w.project_name}\n"
                f"Hackathon: {hackathon}\n"
                f"Team: {w.team_name or w.founder_name}\n"
                f"Prize/Placement: {placement}\n"
                f"Project URL: {w.project_url or purl}\n"
                f"GitHub: {w.github_url or 'not disclosed'}\n\n"
                f"Description: {w.description}"
            )
            candidates.append(
                {
                    "source": "devpost",
                    "founder_name": w.founder_name,
                    "company": w.project_name,
                    "application_text": application_text,
                    "reference_url": w.project_url or purl,
                    "devpost_extras": {
                        "hackathon_name": hackathon,
                        "team_name": w.team_name,
                        "prize_or_placement": placement,
                        "github_url": w.github_url,
                    },
                }
            )
            if len(candidates) >= limit_winners:
                return candidates
    return candidates


async def _discover_devpost(
    limit_hackathons: int, limit_winners: int, winners_per_hackathon: int = 3
) -> list[dict[str, Any]]:
    """Devpost hackathon-winner discovery.

    Three-step crawl:
      1. Extract the Devpost hackathons index (ended, Devpost-managed).
      2. For each hackathon, extract landing + gallery to find winning
         project URLs.
      3. For each winning project URL, extract the project page and parse
         the winner details (team, description, GitHub).

    Requires TAVILY_API_KEY. Silently no-ops without it.
    """
    if not TAVILY_API_KEY:
        log.info("TAVILY_API_KEY not set — skipping Devpost discovery.")
        return []

    hackathons = await _fetch_hackathons(limit_hackathons)
    if not hackathons:
        return []

    projects = await _fetch_gallery_project_urls(hackathons, winners_per_hackathon)
    if not projects:
        return []

    return await _fetch_project_details(projects, limit_winners)


# --------------------------------------------------------------------------- #
# Fan-out                                                                     #
# --------------------------------------------------------------------------- #


async def discover_candidates(
    hours: int,
    per_source: int,
    devpost_limit: int = 8,
) -> list[dict[str, Any]]:
    """Fan out to all discovery sources."""
    show_hn, gh, devpost = await asyncio.gather(
        _discover_show_hn(hours, per_source),
        _discover_github_trending(hours, per_source),
        _discover_devpost(limit_hackathons=per_source, limit_winners=devpost_limit),
    )
    # Dedupe by (founder_name, company)
    seen = set()
    unique: list[dict[str, Any]] = []
    for c in show_hn + gh + devpost:
        key = (c["founder_name"].lower(), c["company"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique


# --------------------------------------------------------------------------- #
# Pipeline invocation                                                         #
# --------------------------------------------------------------------------- #


def _json_default(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def run_pipeline_on_candidate(
    graph, candidate: dict[str, Any], thesis_config: dict[str, Any]
) -> dict[str, Any]:
    state = initial_state(
        founder_name=candidate["founder_name"],
        company=candidate["company"],
        application_text=candidate["application_text"],
        thesis_config=thesis_config,
        is_outbound=True,
    )
    final_state = await graph.ainvoke(state)
    return {
        "candidate_source": candidate.get("source"),
        "reference_url": candidate.get("reference_url"),
        "devpost_extras": candidate.get("devpost_extras"),
        "final_state": final_state,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Outbound founder discovery scan.")
    parser.add_argument("--hours", type=int, default=168, help="Look-back window in hours (default 7 days)")
    parser.add_argument("--per-source", type=int, default=5, help="Max candidates per discovery source")
    parser.add_argument("--devpost-limit", type=int, default=8, help="Max Devpost winners to pull")
    parser.add_argument("--limit", type=int, default=10, help="Max total candidates to run through the pipeline")
    parser.add_argument("--out-dir", type=Path, default=Path("demo_data/outbound"), help="Where to write per-candidate JSON")
    parser.add_argument("--only", choices=["hn", "github", "devpost"], default=None, help="Restrict discovery to a single source")
    args = parser.parse_args()

    logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    log.info(
        "Discovering candidates (hours=%d, per_source=%d, devpost_limit=%d, only=%s)...",
        args.hours, args.per_source, args.devpost_limit, args.only or "all",
    )

    if args.only == "hn":
        candidates = await _discover_show_hn(args.hours, args.per_source)
    elif args.only == "github":
        candidates = await _discover_github_trending(args.hours, args.per_source)
    elif args.only == "devpost":
        candidates = await _discover_devpost(args.per_source, args.devpost_limit)
    else:
        candidates = await discover_candidates(
            args.hours, args.per_source, devpost_limit=args.devpost_limit
        )

    log.info("Discovered %d unique candidates.", len(candidates))
    candidates = candidates[: args.limit]

    thesis_config = {
        "sectors": ["dev tools", "AI infra"],
        "stage": "pre-seed",
        "geography": ["US", "EU"],
        "check_size": [25_000, 150_000],
        "ownership_target": 0.05,
        "risk_appetite": "high",
    }

    graph = build_graph()

    results = []
    for i, c in enumerate(candidates, 1):
        log.info(
            "[%d/%d] %s / %s (%s)",
            i, len(candidates), c["founder_name"], c["company"], c["source"],
        )
        try:
            result = await run_pipeline_on_candidate(graph, c, thesis_config)
        except Exception as e:
            log.warning("Pipeline failed for %s: %s", c["company"], e)
            continue
        results.append(result)
        slug = "".join(ch if ch.isalnum() else "_" for ch in c["company"].lower())[:40]
        path = args.out_dir / f"{i:02d}_{c['source']}_{slug}.json"
        path.write_text(json.dumps(result, default=_json_default, indent=2))
        log.info("  wrote %s", path)

    summary_path = args.out_dir / "_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "scanned_at": datetime.now(timezone.utc).isoformat(),
                "hours_window": args.hours,
                "candidates_discovered": len(candidates),
                "candidates_processed": len(results),
                "candidates": [
                    {
                        "founder_name": r["final_state"]["founder_name"],
                        "company": r["final_state"]["company"],
                        "source": r.get("candidate_source"),
                        "screen_status": r["final_state"].get("screen_status"),
                        "preliminary_score": r["final_state"].get("preliminary_score"),
                        "outreach_drafted": r["final_state"].get("outreach_draft") is not None,
                        "reference_url": r["reference_url"],
                        "devpost_extras": r.get("devpost_extras"),
                    }
                    for r in results
                ],
            },
            indent=2,
        )
    )
    print(f"Wrote {summary_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))