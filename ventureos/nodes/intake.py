"""Intake node — parses the application into hints for downstream tools.

Handle-discovery is now search-driven (Tavily site:github.com) rather than
name-derived guessing. Explicit github.com URLs in the deck still take
absolute priority; search-discovered handles fill in the rest.
"""

from __future__ import annotations

import re
from typing import Any

from ventureos.config import OPENAI_API_KEY
from ventureos.llm import fast_model, openai_json
from ventureos.models import IntakeSummary
from ventureos.nodes._helpers import log_error, log_reason, node_trace
from ventureos.prompts import load_prompt
from ventureos.state import GraphState
from ventureos.tools.github_discovery import discover_github_handles

_GH_URL = re.compile(r"github\.com/([A-Za-z0-9\-_]+)")
_RESEARCH_HINTS = re.compile(
    r"\b(PhD|Ph\.D\.?|arXiv|paper(s)?|publication(s)?|researcher|author of)\b",
    re.IGNORECASE,
)


def _explicit_url_handles(text: str) -> list[str]:
    """Extract handles from any explicit `github.com/xxx` URL in the deck."""
    hits = _GH_URL.findall(text or "")
    # Dedupe preserving order, lowercase
    seen: list[str] = []
    for h in hits:
        h_low = h.lower()
        if h_low and h_low not in seen:
            seen.append(h_low)
    return seen


async def intake_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "intake") as t:
        text = state.get("application_text", "") or ""
        name = state.get("founder_name", "") or ""
        company = state.get("company", "") or ""

        # Step 1: explicit URL handles from deck — authoritative
        explicit_handles = _explicit_url_handles(text)

        # Step 2: Tavily site:github.com discovery
        try:
            search_handles, source_note = await discover_github_handles(
                name, company, max_handles=3
            )
        except Exception as e:
            log_error(state, "intake", f"github_discovery raised: {e}")
            search_handles, source_note = [], f"github_discovery error: {e}"

        # Merge: explicit first, then search, dedupe, cap at 5
        merged: list[str] = []
        for h in explicit_handles + search_handles:
            if h not in merged:
                merged.append(h)
            if len(merged) >= 5:
                break

        is_research = bool(_RESEARCH_HINTS.search(text))

        base_summary = IntakeSummary(
            github_handle_hints=merged,
            research_domain=None,
            is_research_founder=is_research,
            category_labels=[],
            product_urls=[],
            location_hint=None,
        )

        # Step 3: LLM enrichment for category_labels, product_urls,
        # location_hint, research_domain (LLM no longer touches handles)
        summary = base_summary
        if OPENAI_API_KEY and len(text.strip()) >= 40:
            try:
                enriched = await openai_json(
                    system=load_prompt("intake"),
                    user={
                        "founder_name": name,
                        "company": company,
                        "application_text": text[:6000],
                    },
                    schema=IntakeSummary,
                    model=fast_model(),
                )
                # Merge: keep our handles (search-derived is more reliable than LLM guesses)
                summary = IntakeSummary(
                    github_handle_hints=merged,
                    research_domain=enriched.research_domain or base_summary.research_domain,
                    is_research_founder=base_summary.is_research_founder or enriched.is_research_founder,
                    category_labels=enriched.category_labels or base_summary.category_labels,
                    product_urls=enriched.product_urls or base_summary.product_urls,
                    location_hint=enriched.location_hint or base_summary.location_hint,
                )
                t["llm_enriched"] = True
            except Exception as e:
                log_error(state, "intake", f"LLM enrichment failed, using regex only: {e}")
                t["llm_enriched"] = False
        else:
            t["llm_enriched"] = False

        t["gh_hints"] = len(summary.github_handle_hints)
        t["explicit_url_handles"] = explicit_handles
        t["search_handles"] = search_handles
        t["is_research"] = summary.is_research_founder
        t["categories"] = summary.category_labels

        # Build a source-annotated reasoning string
        sources_parts: list[str] = []
        if explicit_handles:
            sources_parts.append(f"{len(explicit_handles)} from deck URL(s)")
        if search_handles:
            sources_parts.append(f"{len(search_handles)} from Tavily search")
        source_summary = " + ".join(sources_parts) if sources_parts else "no handles found"

        log_reason(
            state,
            "intake",
            f"{len(summary.github_handle_hints)} GitHub handle hints ({source_summary}) — "
            f"{source_note}. Categories={summary.category_labels}, "
            f"is_research={summary.is_research_founder}, "
            f"llm_enriched={t.get('llm_enriched')}",
        )
        return {"intake": summary}