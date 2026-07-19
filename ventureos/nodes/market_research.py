"""Market research node — competitor discovery + sizing + stance.

Runs in parallel:
    - SerpAPI open search: "{category} alternatives"
    - SerpAPI open search: "{category} market size TAM"
    - Tavily market query: "{category} competitors 2026"

Then one gpt-4o synthesis call producing MarketResearch. If categories are
missing (intake didn't infer any), returns None gracefully (UI will show
"[Not Disclosed]" per contract).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ventureos.llm import openai_json, smart_model
from ventureos.models import EvidenceItem, MarketResearch
from ventureos.nodes._helpers import log_error, log_reason, node_trace
from ventureos.prompts import load_prompt
from ventureos.state import GraphState
from ventureos.tools.serpapi_tool import serpapi_open_search
from ventureos.tools.tavily_tool import tavily_market_query


def _primary_category(state: GraphState) -> str | None:
    """Pick the first category label from intake (or attributes) as the
    primary market focus."""
    intake = state.get("intake")
    if intake and intake.category_labels:
        return intake.category_labels[0]
    attrs = state.get("attributes")
    if attrs and attrs.categories:
        return attrs.categories[0]
    return None


async def market_research_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "market_research") as t:
        category = _primary_category(state)
        founder = state.get("founder_name", "")

        if not category:
            t["skipped"] = True
            log_reason(
                state,
                "market_research",
                "No category label available; market research skipped.",
            )
            return {"market_research": None}

        # Fan out three parallel queries
        async def _serp(query: str):
            try:
                return await serpapi_open_search(query, founder, num=8)
            except Exception as e:
                log_error(state, "market_research", f"SerpAPI raised: {e}", query=query)
                return []

        async def _tav(query: str):
            try:
                return await tavily_market_query(query, founder)
            except Exception as e:
                log_error(state, "market_research", f"Tavily raised: {e}", query=query)
                return []

        alternatives_q = f'"{category}" alternatives'
        tam_q = f'"{category}" market size TAM'
        competitors_q = f"{category} competitors 2026"

        results = await asyncio.gather(
            _serp(alternatives_q),
            _serp(tam_q),
            _tav(competitors_q),
            return_exceptions=False,
        )

        # Flatten & filter to items with content
        collected: list[EvidenceItem] = []
        for group in results:
            for item in group:
                collected.append(item)

        # Build the LLM payload — pass raw_content for each hit
        payload = {
            "category": category,
            "company": state.get("company"),
            "queries": {
                "alternatives": alternatives_q,
                "market_size": tam_q,
                "competitors": competitors_q,
            },
            "evidence": [
                {
                    "source_type": ev.source_type,
                    "source_url": ev.source_url,
                    "query_used": ev.query_used,
                    "status": ev.status,
                    "raw_content": ev.raw_content,
                }
                for ev in collected
            ],
        }

        try:
            mr = await openai_json(
                system=load_prompt("market_research"),
                user=payload,
                schema=MarketResearch,
                model=smart_model(),
            )
        except Exception as e:
            log_error(state, "market_research", f"LLM synthesis failed: {e}")
            mr = MarketResearch(
                competitors=[],
                market_size_estimate=None,
                stance="neutral",
                reasoning="Synthesis failed; defaulting to neutral.",
                evidence_refs=[],
            )

        # Merge market evidence into raw_evidence so trust scoring can see it
        merged_evidence = list(state.get("raw_evidence", []) or []) + collected

        t["category"] = category
        t["queries_fired"] = 3
        t["evidence_collected"] = len(collected)
        t["competitors_found"] = len(mr.competitors)
        t["stance"] = mr.stance

        log_reason(
            state,
            "market_research",
            f"Category={category} → {len(mr.competitors)} competitors, "
            f"stance={mr.stance}, market_size={mr.market_size_estimate or '[Not Disclosed]'}",
        )
        return {"market_research": mr, "raw_evidence": merged_evidence}