"""Verification node — cross-source claim comparison + contradiction detection.

Approach:
1. Group claims by predicate.
2. For each group with 2+ claims from ≥2 distinct source_types, one gpt-4o
   call comparing them → VerificationResult.
3. Solo claims (single-source, or single-claim groups) are marked
   `unverifiable` — unless the source is a high-trust type (github, s2)
   in which case they are `verified` on strength of source alone.
4. High-stakes solo claims (funding, traction, revenue) trigger a
   Tavily-verify fallback: one more search to look for corroborating text
   on the open web. If Tavily returns something, the claim graduates to
   `verified` and a new EvidenceItem is appended to state.

Absence claims (predicates ending in `_absence`) are always `unverifiable`
by definition; we don't fire LLM calls for them.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Literal

from ventureos.llm import openai_json, smart_model
from ventureos.models import Claim, Contradiction, EvidenceItem, VerificationResult
from ventureos.nodes._helpers import log_error, log_reason, node_trace
from ventureos.prompts import load_prompt
from ventureos.state import GraphState
from ventureos.tools.tavily_tool import tavily_verify

log = logging.getLogger("ventureos.verification")

# Source types considered independently authoritative when a claim stands alone.
_HIGH_TRUST_SOURCES = {"github", "semantic_scholar"}

# Predicates that trigger a Tavily-verify fallback for solo claims (high-stakes,
# quantitative, and easily disputed).
_HIGH_STAKES_PREDICATES = {
    "funding_raised",
    "funding_target",
    "traction_metric",
    "revenue",
    "arr",
    "mrr",
}


def _is_absence(predicate: str) -> bool:
    return predicate.endswith("_absence")


async def _verify_group(
    predicate: str, claims: list[Claim], state: GraphState
) -> VerificationResult:
    """Run one LLM verification call over a predicate-grouped claim list."""
    system = load_prompt("verification")
    user_payload = {
        "founder_name": state.get("founder_name"),
        "company": state.get("company"),
        "predicate": predicate,
        "claims": [
            {
                "id": c.id,
                "text": c.text,
                "value": c.value,
                "source_type": c.source_type,
                "confidence": c.confidence,
            }
            for c in claims
        ],
    }
    try:
        return await openai_json(
            system=system,
            user=user_payload,
            schema=VerificationResult,
            model=smart_model(),
        )
    except Exception as e:
        log_error(state, "verification", f"LLM verification failed: {e}", predicate=predicate)
        # Fall back to marking everything unverifiable
        return VerificationResult(
            verified_ids=[],
            unverifiable_ids=[c.id for c in claims],
            contradictions=[],
        )


async def _tavily_fallback(
    claim: Claim, state: GraphState
) -> tuple[Literal["verified", "unverifiable"], EvidenceItem | None]:
    """For a high-stakes solo claim: try to corroborate via Tavily."""
    company = state.get("company", "")
    founder = state.get("founder_name", "")
    try:
        items = await tavily_verify(claim.text, company, founder)
    except Exception as e:
        log_error(state, "verification", f"Tavily verify raised: {e}", claim_id=claim.id)
        return "unverifiable", None
    if not items:
        return "unverifiable", None
    item = items[0]
    if item.status == "ok":
        # Something came back — treat as corroboration, add to evidence trail
        return "verified", item
    return "unverifiable", item if item.status != "error" else None


async def verification_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "verification") as t:
        claims: list[Claim] = list(state.get("claims", []) or [])
        if not claims:
            log_reason(state, "verification", "No claims to verify.")
            return {"verification_map": {}, "contradictions": []}

        # Group by predicate
        groups: dict[str, list[Claim]] = defaultdict(list)
        for c in claims:
            groups[c.predicate].append(c)

        verification_map: dict[str, str] = {}
        contradictions: list[Contradiction] = []

        # Groups worth LLM verification: 2+ claims from ≥2 distinct sources,
        # non-absence predicates only.
        llm_group_keys: list[str] = []
        solo_or_same_source: list[Claim] = []

        for predicate, group in groups.items():
            if _is_absence(predicate):
                for c in group:
                    verification_map[c.id] = "unverifiable"
                continue
            distinct_sources = {c.source_type for c in group}
            if len(group) >= 2 and len(distinct_sources) >= 2:
                llm_group_keys.append(predicate)
            else:
                solo_or_same_source.extend(group)

        # Run all LLM verifications in parallel
        llm_results = await asyncio.gather(
            *[_verify_group(k, groups[k], state) for k in llm_group_keys],
            return_exceptions=False,
        )
        for k, result in zip(llm_group_keys, llm_results):
            for cid in result.verified_ids:
                verification_map[cid] = "verified"
            for cid in result.unverifiable_ids:
                verification_map[cid] = "unverifiable"
            for pair in result.contradictions:
                verification_map[pair.a] = "contradicted"
                verification_map[pair.b] = "contradicted"
                contradictions.append(
                    Contradiction(
                        claim_id_a=pair.a,
                        claim_id_b=pair.b,
                        description=pair.description,
                        predicate=k,
                    )
                )

        # Handle solo / same-source claims:
        #  - high-trust source → verified on strength of source
        #  - high-stakes predicate → Tavily verify fallback (in parallel)
        #  - otherwise → unverifiable
        tavily_targets: list[Claim] = []
        for c in solo_or_same_source:
            if c.source_type in _HIGH_TRUST_SOURCES:
                verification_map[c.id] = "verified"
            elif c.predicate in _HIGH_STAKES_PREDICATES:
                tavily_targets.append(c)
            else:
                verification_map[c.id] = "unverifiable"

        # Fire Tavily fallbacks in parallel (cap at 5 to protect API budget)
        capped = tavily_targets[:5]
        for c in tavily_targets[5:]:
            verification_map[c.id] = "unverifiable"

        fallback_results = await asyncio.gather(
            *[_tavily_fallback(c, state) for c in capped],
            return_exceptions=False,
        )

        # Merge Tavily-produced EvidenceItems back into raw_evidence
        new_evidence: list[EvidenceItem] = []
        for c, (status, item) in zip(capped, fallback_results):
            verification_map[c.id] = status
            if item is not None:
                new_evidence.append(item)

        # Ensure every original claim ID landed somewhere
        for c in claims:
            verification_map.setdefault(c.id, "unverifiable")

        # Merge new evidence with existing
        merged_evidence = list(state.get("raw_evidence", []) or []) + new_evidence

        t["groups_llm"] = len(llm_group_keys)
        t["solo_claims"] = len(solo_or_same_source)
        t["tavily_fallbacks"] = len(capped)
        t["contradictions"] = len(contradictions)
        t["new_evidence_from_verify"] = len(new_evidence)

        log_reason(
            state,
            "verification",
            f"{len(llm_group_keys)} LLM groups, {len(capped)} Tavily fallbacks, "
            f"{len(contradictions)} contradictions flagged.",
        )
        return {
            "verification_map": verification_map,
            "contradictions": contradictions,
            "raw_evidence": merged_evidence,
        }