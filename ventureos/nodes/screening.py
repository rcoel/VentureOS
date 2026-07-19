"""Screening node — first-pass PASS/FAIL filter before sourcing.

Hybrid:
1. Hard rule: empty company or <20 chars of application text → immediate FAIL.
2. If a key is configured and text is meaningful, one gpt-4o-mini call
   applying the screening prompt returns a ScreeningDecision.
3. Screening is deliberately permissive — the goal is to skip clearly
   non-viable applications, not to be picky.
"""

from __future__ import annotations

from typing import Any

from ventureos.config import OPENAI_API_KEY
from ventureos.llm import fast_model, openai_json
from ventureos.models import ScreeningDecision
from ventureos.nodes._helpers import log_error, log_reason, node_trace
from ventureos.prompts import load_prompt
from ventureos.state import GraphState

MIN_TEXT_CHARS = 20


async def screening_node(state: GraphState) -> dict[str, Any]:
    with node_trace(state, "screening") as t:
        company = (state.get("company") or "").strip()
        text = (state.get("application_text") or "").strip()

        # Hard-rule FAILs
        if not company:
            log_reason(state, "screening", "FAIL: No company name provided.")
            t["status"] = "FAIL"
            t["mode"] = "hard_rule"
            return {"screen_status": "FAIL", "screen_reason": "No company name provided."}
        if len(text) < MIN_TEXT_CHARS:
            log_reason(state, "screening", "FAIL: Application text too short.")
            t["status"] = "FAIL"
            t["mode"] = "hard_rule"
            return {
                "screen_status": "FAIL",
                "screen_reason": "Application text is too short to evaluate.",
            }

        # LLM decision
        if OPENAI_API_KEY:
            try:
                decision = await openai_json(
                    system=load_prompt("screening"),
                    user={
                        "founder_name": state.get("founder_name"),
                        "company": company,
                        "application_text": text[:6000],
                    },
                    schema=ScreeningDecision,
                    model=fast_model(),
                )
                t["status"] = decision.status
                t["mode"] = "llm"
                log_reason(state, "screening", f"{decision.status}: {decision.reason}")
                return {"screen_status": decision.status, "screen_reason": decision.reason}
            except Exception as e:
                log_error(state, "screening", f"LLM screening failed, defaulting PASS: {e}")

        # Default when no API key or LLM failed: PASS (permissive)
        t["status"] = "PASS"
        t["mode"] = "default_pass"
        log_reason(state, "screening", "PASS (default without LLM call).")
        return {
            "screen_status": "PASS",
            "screen_reason": "Basic coherence check passed (no LLM verification available).",
        }


def screening_router(state: GraphState) -> str:
    """PASS → 'sourcing', FAIL → END. Kept here for backward compat; graph.py
    now uses an inline lambda."""
    return "sourcing" if state.get("screen_status") == "PASS" else "__end__"