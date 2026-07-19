"""Attributes rollup node — turns claims into typed FounderAttributes.

Single gpt-4o-mini call over all claims + intake context, producing the
FounderAttributes object that enables compound natural-language queries.

The key discipline is in the prompt: unknowns MUST be `null`, not false/0.
"""

from __future__ import annotations

from typing import Any

from ventureos.llm import fast_model, openai_json
from ventureos.models import FounderAttributes
from ventureos.nodes._helpers import log_error, log_reason, node_trace
from ventureos.prompts import load_prompt
from ventureos.state import GraphState


async def attributes_rollup_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "attributes_rollup") as t:
        claims = list(state.get("claims", []) or [])
        verification_map = state.get("verification_map", {}) or {}
        intake = state.get("intake")

        if not claims and not intake:
            log_reason(
                state, "attributes_rollup", "No claims or intake data; skipping LLM call."
            )
            return {"attributes": FounderAttributes()}

        system = load_prompt("attributes_rollup")
        user_payload = {
            "founder_name": state.get("founder_name"),
            "company": state.get("company"),
            "intake": {
                "category_labels": intake.category_labels if intake else [],
                "location_hint": intake.location_hint if intake else None,
                "is_research_founder": intake.is_research_founder if intake else False,
                "research_domain": intake.research_domain if intake else None,
            },
            "claims": [
                {
                    "id": c.id,
                    "text": c.text,
                    "subject": c.subject,
                    "predicate": c.predicate,
                    "value": c.value,
                    "source_type": c.source_type,
                    "verification_status": verification_map.get(c.id, "unverifiable"),
                }
                for c in claims
            ],
        }

        try:
            attrs = await openai_json(
                system=system,
                user=user_payload,
                schema=FounderAttributes,
                model=fast_model(),
            )
        except Exception as e:
            log_error(state, "attributes_rollup", f"LLM rollup failed: {e}")
            attrs = FounderAttributes()

        # Enrichment: bubble up intake categories if the LLM didn't
        if intake and intake.category_labels and not attrs.categories:
            attrs.categories = intake.category_labels

        t["claims_in"] = len(claims)
        t["is_technical"] = attrs.is_technical
        t["categories"] = attrs.categories
        t["accelerator_tier"] = attrs.accelerator_tier

        log_reason(
            state,
            "attributes_rollup",
            f"Rolled {len(claims)} claims → is_technical={attrs.is_technical}, "
            f"categories={attrs.categories}, accelerator={attrs.accelerator_tier}, "
            f"h_index={attrs.h_index}",
        )
        return {"attributes": attrs}