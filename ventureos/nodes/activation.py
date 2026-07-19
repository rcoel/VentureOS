"""Activation node — outreach draft for outbound-sourced high-signal founders.

Two things happen here:
1. Compute `preliminary_score` (internal routing heuristic — never displayed).
2. If is_outbound AND preliminary_score >= threshold → one gpt-4o call to
   draft a cold outreach email, stored in state["outreach_draft"].

Person B's real founder_score is computed separately and downstream of the
pipeline. The naming is deliberate: `preliminary_score` never appears in UI.
"""

from __future__ import annotations

from typing import Any

from ventureos.llm import openai_json, smart_model
from ventureos.models import OutreachDraft
from ventureos.nodes._helpers import log_error, log_reason, node_trace
from ventureos.prompts import load_prompt
from ventureos.state import GraphState

ACTIVATION_THRESHOLD = 60.0


# Predicates that indicate "we searched but this evidence doesn't apply to
# our founder" — they should NOT count as signal toward outreach.
_NEGATIVE_PREDICATES = {
    "identity_mismatch",
    "s2_no_match",
    "narrative_absence",
    "site_absence",
    "hn_absence",
    "s2_absence",
    "github_absence",
}


def _preliminary_score(state: GraphState) -> float:
    """Cheap heuristic used ONLY for routing to activation.

    Signals:
    - Verified positive claims (identity confirmed, execution signals)
      weighted higher than unverifiable.
    - Distinct source_type count (source diversity) — but only sources
      that produced positive evidence, not just "not_found" hits.
    - Contradiction penalty.
    - Identity-mismatch / absence claims explicitly DO NOT contribute — a
      pipeline that searched 5 places and confirmed nothing about our
      founder should not score 60.
    """
    claims = state.get("claims", []) or []
    verification_map = state.get("verification_map", {}) or {}
    contradictions = state.get("contradictions", []) or []
    evidence = state.get("raw_evidence", []) or []

    if not claims:
        return 0.0

    positive_claims = [c for c in claims if c.predicate not in _NEGATIVE_PREDICATES]
    if not positive_claims:
        return 0.0

    verified = sum(
        1 for c in positive_claims if verification_map.get(c.id) == "verified"
    )
    unverif = sum(
        1 for c in positive_claims if verification_map.get(c.id) == "unverifiable"
    )

    # Only count source types that actually produced a positive claim
    positive_sources = {c.source_type for c in positive_claims}
    distinct_sources = len(positive_sources)

    # Extra penalty: identity_mismatch claims are strongly negative signal
    identity_mismatches = sum(
        1 for c in claims if c.predicate in {"identity_mismatch", "s2_no_match"}
    )

    verified_component = min(verified * 12.0, 55.0)
    unverif_component = min(unverif * 2.0, 15.0)
    diversity_component = min(distinct_sources * 5.0, 25.0)
    contradiction_penalty = min(len(contradictions) * 12.0, 40.0)
    identity_penalty = min(identity_mismatches * 15.0, 45.0)

    score = (
        verified_component
        + unverif_component
        + diversity_component
        - contradiction_penalty
        - identity_penalty
    )
    return max(0.0, min(100.0, score))


def _strongest_signals(state: GraphState) -> list[dict[str, Any]]:
    """Compact summary of verified claims for the outreach prompt."""
    claims = state.get("claims", []) or []
    vmap = state.get("verification_map", {}) or {}
    verified_claims = [c for c in claims if vmap.get(c.id) == "verified"]
    return [
        {
            "predicate": c.predicate,
            "text": c.text,
            "value": c.value,
            "source_type": c.source_type,
        }
        for c in verified_claims[:8]
    ]


async def activation_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "activation") as t:
        prelim = _preliminary_score(state)
        is_outbound = bool(state.get("is_outbound", False))
        should_draft = is_outbound and prelim >= ACTIVATION_THRESHOLD

        outreach: str | None = None
        if should_draft:
            attrs = state.get("attributes")
            mr = state.get("market_research")
            contradictions = state.get("contradictions", []) or []

            payload = {
                "founder_name": state.get("founder_name"),
                "company": state.get("company"),
                "primary_category": (attrs.categories[0] if attrs and attrs.categories else None),
                "market_stance": mr.stance if mr else "neutral",
                "market_reasoning": mr.reasoning if mr else None,
                "strongest_signals": _strongest_signals(state),
                "contradictions": [
                    {"description": c.description, "predicate": c.predicate}
                    for c in contradictions
                ],
                "preliminary_score": prelim,
            }
            try:
                draft = await openai_json(
                    system=load_prompt("activation"),
                    user=payload,
                    schema=OutreachDraft,
                    model=smart_model(),
                )
                outreach = draft.outreach_draft
            except Exception as e:
                log_error(state, "activation", f"Outreach draft LLM failed: {e}")

        t["preliminary_score"] = prelim
        t["is_outbound"] = is_outbound
        t["draft_generated"] = outreach is not None

        log_reason(
            state,
            "activation",
            f"preliminary_score={prelim:.1f}, is_outbound={is_outbound}, "
            f"drafted={outreach is not None}",
        )
        return {"preliminary_score": prelim, "outreach_draft": outreach}