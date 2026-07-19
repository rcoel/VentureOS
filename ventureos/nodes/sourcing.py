"""Sourcing node — fans out to all 5 tools in parallel via asyncio.gather.

Invariants:
1. Never raises up. Any tool failure appends to state["errors"] and returns [].
2. Empty results still get recorded as EvidenceItem(status="not_found") so
   cold-start reweighting downstream can tell "never searched" from "searched,
   nothing there".
3. Semantic Scholar is called conditionally — only when intake flagged
   is_research_founder=True (or a research_domain is set).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ventureos.models import EvidenceItem
from ventureos.nodes._helpers import log_error, log_reason, node_trace
from ventureos.state import GraphState
from ventureos.tools.github import fetch_github
from ventureos.tools.hn import fetch_hn
from ventureos.tools.semantic_scholar import fetch_author, fetch_papers_by_domain
from ventureos.tools.serpapi_tool import serpapi_site_search
from ventureos.tools.tavily_tool import tavily_context


async def _safe(coro, node: str, tool: str, state: GraphState) -> list[EvidenceItem]:
    """Run a tool coroutine, catch any exception, log to state.errors."""
    try:
        return await coro
    except Exception as e:  # never crash the graph
        log_error(state, node, f"{tool} raised: {e}", tool=tool)
        return []


async def sourcing_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "sourcing") as t:
        name = state.get("founder_name", "")
        company = state.get("company", "")
        intake = state.get("intake")

        # === Build task list ===
        tasks: list = []

        # 1. GitHub — one call per candidate handle (up to 3)
        gh_hints = intake.github_handle_hints if intake else []
        for handle in gh_hints[:3]:
            tasks.append(_safe(fetch_github(handle, name), "sourcing", f"github:{handle}", state))

        # 2. HN — always try, dirt cheap and no auth
        if company:
            tasks.append(_safe(fetch_hn(name, company), "sourcing", "hn", state))

        # 3. Tavily — general narrative context
        tasks.append(_safe(tavily_context(name, company), "sourcing", "tavily_context", state))

        # 4. SerpAPI — site-restricted queries for sources we don't have direct APIs for
        if company:
            tasks.append(
                _safe(
                    serpapi_site_search("producthunt.com", f'"{company}"', name),
                    "sourcing", "serpapi:producthunt", state,
                )
            )
            tasks.append(
                _safe(
                    serpapi_site_search("ycombinator.com/companies", f'"{company}"', name),
                    "sourcing", "serpapi:yc", state,
                )
            )
        if name:
            tasks.append(
                _safe(
                    serpapi_site_search("linkedin.com/in", f'"{name}" {company}'.strip(), name),
                    "sourcing", "serpapi:linkedin", state,
                )
            )

        # 5. Semantic Scholar — conditional
        is_research = bool(intake and (intake.is_research_founder or intake.research_domain))
        if is_research and name:
            tasks.append(_safe(fetch_author(name, name), "sourcing", "s2:author", state))
            domain = intake.research_domain if intake else None
            if domain:
                tasks.append(
                    _safe(fetch_papers_by_domain(domain, name), "sourcing", "s2:papers", state)
                )

        # === Fan out ===
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Flatten — each result is already a list[EvidenceItem]
        raw_evidence: list[EvidenceItem] = []
        for group in results:
            if isinstance(group, list):
                raw_evidence.extend(group)

        # Bucket for the reasoning log
        by_source: dict[str, int] = {}
        ok_by_source: dict[str, int] = {}
        for item in raw_evidence:
            by_source[item.source_type] = by_source.get(item.source_type, 0) + 1
            if item.status == "ok":
                ok_by_source[item.source_type] = ok_by_source.get(item.source_type, 0) + 1

        t["tasks"] = len(tasks)
        t["evidence_count"] = len(raw_evidence)
        t["by_source"] = by_source
        t["ok_by_source"] = ok_by_source
        t["research_branch"] = is_research

        log_reason(
            state,
            "sourcing",
            f"Fanned {len(tasks)} tool calls → {len(raw_evidence)} evidence items "
            f"({ok_by_source} with content).",
        )
        return {"raw_evidence": raw_evidence}