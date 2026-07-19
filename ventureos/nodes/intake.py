"""Intake node — parses the application into hints for downstream tools.

Hybrid approach:
1. Regex pre-pass extracts guaranteed signals (explicit GitHub URLs from
   text, research keyword hits). This runs always, even without an API key.
2. If OpenAI is configured AND application_text is non-trivial, one small
   gpt-4o-mini call enriches the summary with category_labels, product_urls,
   location_hint, and a research_domain.
3. Regex + LLM results are merged (LLM never overrides an explicit URL match).
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

_GH_URL = re.compile(r"github\.com/([A-Za-z0-9\-_]+)")
_RESEARCH_HINTS = re.compile(
    r"\b(PhD|Ph\.D\.?|arXiv|paper(s)?|publication(s)?|researcher|author of)\b",
    re.IGNORECASE,
)


def _guess_handles(name: str) -> list[str]:
    """Derive plausible GitHub handle candidates from a founder name.

    Rules:
    - If the name is a single token shorter than 5 chars (typical HN handle
      like 'h02', 'gpsmsn'), we treat it as an opaque handle and return it
      as-is with no truncations. Truncating would produce single-letter
      handles that almost always match unrelated users.
    - If the name is a real multi-word name (e.g. 'Maya Chen'), derive
      firstlast / first-last / first.last / first_last combinations.
    - We never emit handles shorter than 3 characters — those are always
      going to be unrelated squatted usernames.
    """
    parts = [p for p in re.split(r"\s+", name.strip().lower()) if p]
    if not parts:
        return []
    if len(parts) == 1:
        # Single token: treat as an opaque handle, no truncations.
        h = parts[0]
        return [h] if len(h) >= 3 else []
    first, *rest = parts
    last = rest[-1] if rest else ""
    candidates: list[str] = []
    if last:
        candidates += [f"{first}{last}", f"{first}-{last}", f"{first}.{last}", f"{first}_{last}"]
    # Only include the first-name-only guess if it's a real name (>= 3 chars)
    if len(first) >= 3:
        candidates.append(first)
    return [c for c in list(dict.fromkeys(candidates)) if len(c) >= 3]


def _regex_summary(state: GraphState) -> IntakeSummary:
    text = state.get("application_text", "") or ""
    name = state.get("founder_name", "")

    url_handles = _GH_URL.findall(text)
    name_handles = _guess_handles(name)
    gh_hints = list(dict.fromkeys(url_handles + name_handles))[:5]

    is_research = bool(_RESEARCH_HINTS.search(text))

    return IntakeSummary(
        github_handle_hints=gh_hints,
        research_domain=None,
        is_research_founder=is_research,
        category_labels=[],
        product_urls=[],
        location_hint=None,
    )


def _merge(base: IntakeSummary, enriched: IntakeSummary) -> IntakeSummary:
    """Combine regex base with LLM enrichment. Base handles + regex research
    flag are authoritative; everything else the LLM contributes."""
    merged_handles = list(
        dict.fromkeys(list(base.github_handle_hints) + list(enriched.github_handle_hints))
    )[:5]
    return IntakeSummary(
        github_handle_hints=merged_handles,
        research_domain=enriched.research_domain or base.research_domain,
        is_research_founder=base.is_research_founder or enriched.is_research_founder,
        category_labels=enriched.category_labels or base.category_labels,
        product_urls=enriched.product_urls or base.product_urls,
        location_hint=enriched.location_hint or base.location_hint,
    )


async def intake_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "intake") as t:
        base = _regex_summary(state)
        text = state.get("application_text", "") or ""

        # LLM enrichment only if we have text worth analyzing AND a key
        if OPENAI_API_KEY and len(text.strip()) >= 40:
            try:
                enriched = await openai_json(
                    system=load_prompt("intake"),
                    user={
                        "founder_name": state.get("founder_name"),
                        "company": state.get("company"),
                        "application_text": text[:6000],  # cap payload
                    },
                    schema=IntakeSummary,
                    model=fast_model(),
                )
                summary = _merge(base, enriched)
                t["llm_enriched"] = True
            except Exception as e:
                log_error(state, "intake", f"LLM enrichment failed, using regex only: {e}")
                summary = base
                t["llm_enriched"] = False
        else:
            summary = base
            t["llm_enriched"] = False

        t["gh_hints"] = len(summary.github_handle_hints)
        t["is_research"] = summary.is_research_founder
        t["categories"] = summary.category_labels

        log_reason(
            state,
            "intake",
            f"{len(summary.github_handle_hints)} GitHub hints, "
            f"categories={summary.category_labels}, is_research={summary.is_research_founder}, "
            f"llm_enriched={t.get('llm_enriched')}",
        )
        return {"intake": summary}