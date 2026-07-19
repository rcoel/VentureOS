"""Extraction node — turns raw evidence into structured Claim objects.

One LLM call per EvidenceItem, run in parallel via asyncio.gather.
Each call uses the source-specific prompt in ventureos/prompts/extraction_{source_type}.md
and OpenAI structured outputs against the ClaimList schema.

Special handling:
- Deck evidence: the pipeline doesn't produce EvidenceItem(source_type="deck")
  from tools; we synthesize one here from state["application_text"] so its
  claims flow through the same extraction path.
- Empty-result evidence (status="not_found"): still passed to the LLM. Prompts
  are instructed to emit an *_absence claim so the cold-start signal survives.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from ventureos.llm import openai_json
from ventureos.models import Claim, ClaimList, EvidenceItem, SourceType
from ventureos.nodes._helpers import log_error, log_reason, node_trace
from ventureos.prompts import load_prompt
from ventureos.state import GraphState

log = logging.getLogger("ventureos.extraction")


def _prompt_for(source_type: SourceType) -> str:
    """Load the source-specific extraction prompt."""
    return load_prompt(f"extraction_{source_type}")


def _synth_deck_evidence(state: GraphState) -> EvidenceItem | None:
    """Wrap the raw application_text as an EvidenceItem so it flows through
    the same extraction pipeline as tool evidence."""
    text = (state.get("application_text") or "").strip()
    if len(text) < 40:
        return None
    return EvidenceItem(
        id=f"ev_deck_{uuid4().hex[:8]}",
        founder_name=state.get("founder_name", ""),
        source_type="deck",
        source_url=None,
        raw_content={"application_text": text},
        query_used="deck",
        status="ok",
    )


async def _extract_one(ev: EvidenceItem, state: GraphState) -> list[Claim]:
    """Run extraction on one EvidenceItem, returning its Claim list.

    Never raises — errors are logged to state.errors and this returns [].
    """
    try:
        system = _prompt_for(ev.source_type)
    except FileNotFoundError as e:
        log_error(state, "extraction", str(e), source_type=ev.source_type)
        return []

    # Give the LLM the raw evidence plus a lightweight context header so it
    # knows which founder/company to disambiguate against.
    user_payload = {
        "founder_name": state.get("founder_name"),
        "company": state.get("company"),
        "evidence_id": ev.id,
        "source_type": ev.source_type,
        "source_url": ev.source_url,
        "query_used": ev.query_used,
        "evidence_status": ev.status,
        "raw_content": ev.raw_content,
    }

    try:
        result = await openai_json(
            system=system,
            user=user_payload,
            schema=ClaimList,
        )
    except Exception as e:
        log_error(state, "extraction", f"LLM extraction failed: {e}", evidence_id=ev.id)
        return []

    # Post-fixups: enforce source_evidence_id, source_type, founder_name AND
    # regenerate a fresh unique id so LLM-generated collisions like "claim_1"
    # can't poison the verification_map. The traceability spine is the tuple
    # (id, source_evidence_id), so id uniqueness is non-negotiable.
    from uuid import uuid4

    claims: list[Claim] = []
    for c in result.claims:
        c.id = f"cl_{uuid4().hex[:12]}"
        c.source_evidence_id = ev.id
        c.source_type = ev.source_type
        # Always overwrite founder_name with the state value — this prevents
        # the LLM from attributing claims about unrelated people (e.g. a
        # different GitHub user whose profile we probed) to our founder.
        c.founder_name = state.get("founder_name", "")
        claims.append(c)
    return claims


async def extraction_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "extraction") as t:
        evidence: list[EvidenceItem] = list(state.get("raw_evidence", []) or [])

        # Fold in a synthesized deck EvidenceItem if we have application text
        deck_ev = _synth_deck_evidence(state)
        if deck_ev is not None:
            evidence.append(deck_ev)

        if not evidence:
            log_reason(state, "extraction", "No evidence to extract from.")
            return {"claims": []}

        # Fan out — one LLM call per evidence item
        results = await asyncio.gather(
            *[_extract_one(ev, state) for ev in evidence],
            return_exceptions=False,
        )
        claims: list[Claim] = [c for group in results for c in group]

        # Also merge the synthesized deck evidence into raw_evidence so
        # downstream nodes and Person B's DB layer see it.
        merged_evidence = list(state.get("raw_evidence", []) or [])
        if deck_ev is not None:
            merged_evidence.append(deck_ev)

        # Per-source claim counts for trace/observability
        by_source: dict[str, int] = {}
        for c in claims:
            by_source[c.source_type] = by_source.get(c.source_type, 0) + 1

        t["evidence_in"] = len(evidence)
        t["claims_out"] = len(claims)
        t["by_source"] = by_source

        log_reason(
            state,
            "extraction",
            f"{len(evidence)} evidence items → {len(claims)} claims ({by_source}).",
        )
        return {"claims": claims, "raw_evidence": merged_evidence}