"""LangGraph state — the single contract every node reads and writes.

Nodes return partial state dicts; LangGraph merges them into the running state.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from ventureos.models import (
    Claim,
    Contradiction,
    EvidenceItem,
    FounderAttributes,
    IntakeSummary,
    MarketResearch,
    SWOTAnalysis,
)


class GraphState(TypedDict, total=False):
    """State passed between LangGraph nodes.

    `total=False` because nodes populate fields incrementally — a partial state
    with only `founder_name`, `company`, `application_text` is valid at entry.
    """

    # === Inputs (populated by the caller) ===
    founder_name: str
    company: str
    application_text: str
    thesis_config: dict[str, Any]
    is_outbound: bool

    # === Intake node ===
    intake: IntakeSummary

    # === Screening node ===
    screen_status: Literal["PASS", "FAIL", "PENDING"]
    screen_reason: str

    # === Sourcing node ===
    raw_evidence: list[EvidenceItem]

    # === Extraction node ===
    claims: list[Claim]

    # === Verification node ===
    contradictions: list[Contradiction]
    verification_map: dict[str, Literal["verified", "unverifiable", "contradicted"]]

    # === Attributes rollup ===
    attributes: FounderAttributes | None

    # === Market research ===
    market_research: MarketResearch | None
    swot_analysis: SWOTAnalysis | None

    # === Activation ===
    preliminary_score: float  # internal only; NEVER displayed. Person B computes founder_score.
    outreach_draft: str | None

    # === Observability ===
    errors: list[dict[str, Any]]
    trace: list[dict[str, Any]]           # per-node timing/tokens
    reasoning_log: list[dict[str, Any]]   # per-node one-line reasoning (Agentic Traceability)


def initial_state(
    founder_name: str,
    company: str,
    application_text: str = "",
    thesis_config: dict[str, Any] | None = None,
    is_outbound: bool = False,
) -> GraphState:
    """Build a fresh GraphState with all collection fields initialized empty."""
    return GraphState(
        founder_name=founder_name,
        company=company,
        application_text=application_text,
        thesis_config=thesis_config or {},
        is_outbound=is_outbound,
        screen_status="PENDING",
        screen_reason="",
        raw_evidence=[],
        claims=[],
        contradictions=[],
        verification_map={},
        attributes=None,
        market_research=None,
        swot_analysis=None,
        preliminary_score=0.0,
        outreach_draft=None,
        errors=[],
        trace=[],
        reasoning_log=[],
    )