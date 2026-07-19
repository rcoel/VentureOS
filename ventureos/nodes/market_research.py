"""Market research + SWOT synthesis node.

Two things happen here:
1. Existing MarketResearch: competitor discovery, market sizing, stance.
2. NEW citation-backed SWOTAnalysis: four SWOT-specific web queries plus
   one gpt-4o synthesis call that produces bullets grounded in real URLs.

Both are attached to state and persisted downstream.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ventureos.llm import openai_json, smart_model
from ventureos.models import (
    Claim,
    EvidenceItem,
    MarketResearch,
    SWOTAnalysis,
)
from ventureos.nodes._helpers import log_error, log_reason, node_trace
from ventureos.prompts import load_prompt
from ventureos.state import GraphState
from ventureos.tools.semantic_scholar import fetch_papers_by_domain
from ventureos.tools.serpapi_tool import serpapi_ai_mode, serpapi_open_search
from ventureos.tools.tavily_tool import tavily_market_query


def _primary_category(state: GraphState) -> str | None:
    intake = state.get("intake")
    if intake and intake.category_labels:
        return intake.category_labels[0]
    attrs = state.get("attributes")
    if attrs and attrs.categories:
        return attrs.categories[0]
    return None


async def _safe_market_search(
    coro, node: str, tool: str, state: GraphState
) -> list[EvidenceItem]:
    try:
        return await coro
    except Exception as e:
        log_error(state, node, f"{tool} raised: {e}", tool=tool)
        return []


def _summarize_evidence_for_llm(items: list[EvidenceItem]) -> list[dict[str, Any]]:
    """Trim raw evidence for the LLM — just title/url/content snippets."""
    out: list[dict[str, Any]] = []
    for ev in items:
        rc = ev.raw_content or {}
        # Common Tavily shape: {"results": [{title, url, content, ...}]}
        # Common SerpAPI shape: {"organic_results": [{title, link, snippet}]}
        for r in (rc.get("results") or [])[:5]:
            out.append({
                "title": r.get("title"),
                "url": r.get("url"),
                "content": (r.get("content") or "")[:600],
                "source_type": ev.source_type,
                "query": ev.query_used,
            })
        for r in (rc.get("organic_results") or [])[:5]:
            out.append({
                "title": r.get("title"),
                "url": r.get("link"),
                "content": (r.get("snippet") or "")[:600],
                "source_type": ev.source_type,
                "query": ev.query_used,
            })
    return out


async def market_research_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "market_research") as t:
        category = _primary_category(state)
        company = state.get("company", "")
        founder = state.get("founder_name", "")

        if not category:
            t["skipped"] = True
            log_reason(
                state, "market_research",
                "No category label available; market research + SWOT skipped.",
            )
            return {"market_research": None, "swot_analysis": None}

        # ------ Google AI Mode primary market-research query ------
        ai_mode_q = (
            f"Market analysis for {category} startups in 2026: competitors, "
            f"total addressable market, key opportunities, and main threats"
            + (f" specifically for {company}" if company else "")
        )

        # ------ Existing market-research queries (fallback + supplemental) ------
        alt_q = f'"{category}" alternatives'
        tam_q = f'"{category}" market size TAM'
        comp_q = f"{category} competitors 2026"

        # ------ SWOT-specific queries ------
        strengths_q = f"{category} successful startups 2026 differentiation"
        weaknesses_q = f'"{company}" limitations risks challenges' if company else f"{category} common failure modes"
        opportunities_q = f"{category} emerging trends 2026 gaps opportunity"
        threats_q = f"{category} market saturation risks crowded"

        # Fan out all queries in parallel (AI Mode + 3 market + 4 SWOT + 1 S2)
        (
            ai_mode_ev,
            alt_ev, tam_ev, comp_ev,
            strengths_ev, weaknesses_ev, opportunities_ev, threats_ev,
            s2_papers_ev,
        ) = await asyncio.gather(
            _safe_market_search(serpapi_ai_mode(ai_mode_q, founder), "market_research", "serp:ai_mode", state),
            _safe_market_search(serpapi_open_search(alt_q, founder, num=8), "market_research", "serp:alt", state),
            _safe_market_search(serpapi_open_search(tam_q, founder, num=8), "market_research", "serp:tam", state),
            _safe_market_search(tavily_market_query(comp_q, founder), "market_research", "tav:comp", state),
            _safe_market_search(tavily_market_query(strengths_q, founder), "market_research", "tav:S", state),
            _safe_market_search(tavily_market_query(weaknesses_q, founder), "market_research", "tav:W", state),
            _safe_market_search(tavily_market_query(opportunities_q, founder), "market_research", "tav:O", state),
            _safe_market_search(tavily_market_query(threats_q, founder), "market_research", "tav:T", state),
            _safe_market_search(fetch_papers_by_domain(category, founder), "market_research", "s2:papers", state),
        )

        # Did AI Mode return useful content?
        ai_mode_ok = bool(ai_mode_ev) and ai_mode_ev[0].status == "ok"
        ai_mode_summary = (
            ai_mode_ev[0].raw_content if ai_mode_ok else {}
        )

        market_evidence: list[EvidenceItem] = []
        for group in (
            ai_mode_ev, alt_ev, tam_ev, comp_ev,
            strengths_ev, weaknesses_ev, opportunities_ev, threats_ev,
            s2_papers_ev,
        ):
            market_evidence.extend(group)

        # ------ Synthesize MarketResearch (with AI Mode as primary if available) ------
        mr_payload = {
            "category": category,
            "company": company,
            "queries": {
                "ai_mode": ai_mode_q,
                "alternatives": alt_q,
                "market_size": tam_q,
                "competitors": comp_q,
            },
            "ai_mode_summary": ai_mode_summary,  # authoritative when non-empty
            "s2_papers": s2_papers_ev[0].raw_content if s2_papers_ev else {},
            "evidence": [
                {
                    "source_type": ev.source_type,
                    "source_url": ev.source_url,
                    "query_used": ev.query_used,
                    "status": ev.status,
                    "raw_content": ev.raw_content,
                }
                for ev in (ai_mode_ev + alt_ev + tam_ev + comp_ev)
            ],
        }
        try:
            mr = await openai_json(
                system=load_prompt("market_research"),
                user=mr_payload,
                schema=MarketResearch,
                model=smart_model(),
            )
        except Exception as e:
            log_error(state, "market_research", f"MarketResearch synth failed: {e}")
            mr = MarketResearch(
                competitors=[], market_size_estimate=None, stance="neutral",
                reasoning="Synthesis failed; defaulting to neutral.", evidence_refs=[],
            )

        # ------ Synthesize SWOTAnalysis (new, citation-backed) ------
        claims: list[Claim] = list(state.get("claims") or [])
        contradictions = list(state.get("contradictions") or [])
        swot_payload = {
            "founder_name": founder,
            "company": company,
            "primary_category": category,
            "market_research": mr.model_dump(),
            "claims": [
                {
                    "id": c.id, "predicate": c.predicate, "text": c.text,
                    "value": c.value, "source_type": c.source_type,
                    "verification_status": state.get("verification_map", {}).get(c.id, "unverifiable"),
                }
                for c in claims[:50]
            ],
            "contradictions": [
                {"description": c.description, "predicate": c.predicate}
                for c in contradictions
            ],
            "swot_evidence": {
                "strengths": _summarize_evidence_for_llm(strengths_ev),
                "weaknesses": _summarize_evidence_for_llm(weaknesses_ev),
                "opportunities": _summarize_evidence_for_llm(opportunities_ev),
                "threats": _summarize_evidence_for_llm(threats_ev),
            },
            "ai_mode_references": ai_mode_summary.get("references", []) if ai_mode_ok else [],
            "s2_papers": s2_papers_ev[0].raw_content if s2_papers_ev else {},
        }
        try:
            swot = await openai_json(
                system=load_prompt("swot_synthesis"),
                user=swot_payload,
                schema=SWOTAnalysis,
                model=smart_model(),
            )
        except Exception as e:
            log_error(state, "market_research", f"SWOT synth failed: {e}")
            swot = SWOTAnalysis()

        merged_evidence = list(state.get("raw_evidence") or []) + market_evidence

        t["category"] = category
        t["queries_fired"] = 9
        t["evidence_collected"] = len(market_evidence)
        t["competitors_found"] = len(mr.competitors)
        t["stance"] = mr.stance
        t["ai_mode_ok"] = ai_mode_ok
        t["ai_mode_refs"] = len(ai_mode_summary.get("references", [])) if ai_mode_ok else 0
        t["swot_bullets"] = (
            len(swot.strengths) + len(swot.weaknesses)
            + len(swot.opportunities) + len(swot.threats)
        )

        ai_mode_note = (
            f"AI Mode: {t['ai_mode_refs']} refs; " if ai_mode_ok
            else "AI Mode: unavailable → fell back to SerpAPI+Tavily; "
        )
        log_reason(
            state, "market_research",
            f"{ai_mode_note}Category={category} → {len(mr.competitors)} competitors, "
            f"stance={mr.stance}, market_size={mr.market_size_estimate or '[Not Disclosed]'}, "
            f"SWOT bullets: {t['swot_bullets']} (S={len(swot.strengths)}, "
            f"W={len(swot.weaknesses)}, O={len(swot.opportunities)}, "
            f"T={len(swot.threats)})",
        )
        return {
            "market_research": mr,
            "swot_analysis": swot,
            "raw_evidence": merged_evidence,
        }