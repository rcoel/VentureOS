"""SWOT view — read persisted citation-backed entries first, fall back to
deterministic derivation only when the pipeline hasn't produced any.

The persisted entries are the source of truth once the pipeline has run
market_research + SWOT synthesis. This module returns objects that carry
the citation URL so the memo and Profile page can render them as
clickable anchors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ventureos_ui.models_orm import (
    Claim,
    Contradiction,
    Founder,
    MarketResearch,
    SWOTEntry,
)
from ventureos_ui.scoring.constants import (
    ABSENCE_PREDICATES,
    EXECUTION_SIGNAL_PREDICATES,
    TRACK_RECORD_PREDICATES,
)


@dataclass
class SWOTItem:
    """One SWOT bullet — with optional citation."""
    text: str
    source_url: str | None = None
    source_title: str | None = None
    reasoning: str = ""


@dataclass
class SWOT:
    strengths: list[SWOTItem] = field(default_factory=list)
    weaknesses: list[SWOTItem] = field(default_factory=list)
    opportunities: list[SWOTItem] = field(default_factory=list)
    threats: list[SWOTItem] = field(default_factory=list)


def _load_persisted(session: Session, founder_id: str) -> SWOT:
    """Read persisted citation-backed SWOTEntry rows."""
    swot = SWOT()
    rows = list(
        session.execute(select(SWOTEntry).where(SWOTEntry.founder_id == founder_id)).scalars()
    )
    quadrant_map = {
        "strength": swot.strengths,
        "weakness": swot.weaknesses,
        "opportunity": swot.opportunities,
        "threat": swot.threats,
    }
    for r in rows:
        target = quadrant_map.get(r.quadrant)
        if target is None:
            continue
        target.append(
            SWOTItem(
                text=r.text,
                source_url=r.source_url,
                source_title=r.source_title,
                reasoning=r.reasoning or "",
            )
        )
    return swot


def _derive_fallback(session: Session, founder_id: str) -> SWOT:
    """Deterministic derivation from claims + market research.

    Only used when no persisted SWOT exists (older founders loaded before
    the pipeline supported citation-backed SWOT).
    """
    founder = session.get(Founder, founder_id)
    if founder is None:
        return SWOT()

    claims: list[Claim] = list(
        session.execute(select(Claim).where(Claim.founder_id == founder_id)).scalars()
    )
    contradictions: list[Contradiction] = list(
        session.execute(
            select(Contradiction).where(Contradiction.founder_id == founder_id)
        ).scalars()
    )
    mr = session.get(MarketResearch, founder_id)

    swot = SWOT()

    strong_predicates = TRACK_RECORD_PREDICATES | EXECUTION_SIGNAL_PREDICATES
    for c in claims:
        if c.verification_status == "verified" and c.predicate in strong_predicates:
            swot.strengths.append(SWOTItem(
                text=f"[{c.predicate}] {c.text} (trust {c.trust_score:.2f})",
                reasoning="Derived from verified claim.",
            ))

    if not swot.strengths:
        for c in claims:
            if c.predicate in strong_predicates and c.verification_status != "contradicted":
                swot.strengths.append(SWOTItem(
                    text=f"[{c.predicate}] {c.text} (unverified, trust {c.trust_score:.2f})",
                    reasoning="Derived from unverified claim.",
                ))
                if len(swot.strengths) >= 3:
                    break

    seen_absence: set[str] = set()
    for c in claims:
        if c.predicate in ABSENCE_PREDICATES and c.predicate not in seen_absence:
            seen_absence.add(c.predicate)
            swot.weaknesses.append(SWOTItem(
                text=f"[{c.predicate}] {c.text}",
                reasoning="Derived from absence claim.",
            ))

    attrs = founder.attributes or {}
    if isinstance(attrs, dict):
        if attrs.get("location") is None:
            swot.weaknesses.append(SWOTItem(text="Location: [Not Disclosed]"))
        if attrs.get("prior_vc_backing") is None:
            swot.weaknesses.append(SWOTItem(text="Prior VC backing: unknown"))
        if attrs.get("customer_segment") is None:
            swot.weaknesses.append(SWOTItem(text="Customer segment: unclear"))
        if attrs.get("is_technical") is None:
            swot.weaknesses.append(SWOTItem(text="Technical background: not confirmed"))

    if mr:
        if mr.stance == "bullish":
            swot.opportunities.append(SWOTItem(
                text=f"Bullish market stance: {mr.reasoning[:180]}",
                reasoning="Derived from MarketResearch.stance.",
            ))
        if mr.market_size_estimate:
            swot.opportunities.append(SWOTItem(
                text=f"Market size estimate: {mr.market_size_estimate}",
                reasoning="Derived from MarketResearch.market_size_estimate.",
            ))
        if mr.competitors and len(mr.competitors) < 4:
            swot.opportunities.append(SWOTItem(
                text=f"Only {len(mr.competitors)} named competitors — potentially uncrowded",
                reasoning="Derived from small competitor count.",
            ))
    else:
        swot.opportunities.append(SWOTItem(
            text="Market research not yet completed — signal pending",
        ))

    for c in contradictions:
        swot.threats.append(SWOTItem(
            text=f"[{c.predicate}] {c.description}",
            reasoning="Derived from contradiction.",
        ))
    if mr and mr.stance == "bear":
        swot.threats.append(SWOTItem(
            text=f"Bearish market stance: {mr.reasoning[:180]}",
            reasoning="Derived from MarketResearch.stance == bear.",
        ))
    if mr and mr.competitors and len(mr.competitors) >= 6:
        swot.threats.append(SWOTItem(
            text=f"Crowded market — {len(mr.competitors)} named competitors identified",
            reasoning="Derived from large competitor count.",
        ))

    # Empty-quadrant guard rails
    if not swot.strengths:
        swot.strengths.append(SWOTItem(text="[Not Disclosed] — no verified positive signals yet"))
    if not swot.weaknesses:
        swot.weaknesses.append(SWOTItem(text="None identified — but evidence base is thin"))
    if not swot.opportunities:
        swot.opportunities.append(SWOTItem(text="[Not Disclosed]"))
    if not swot.threats:
        swot.threats.append(SWOTItem(text="No contradictions or bearish market signals flagged"))

    return swot


def build_swot(session: Session, founder_id: str) -> SWOT:
    """Prefer persisted citation-backed SWOT; fall back to derivation."""
    persisted = _load_persisted(session, founder_id)
    total = (
        len(persisted.strengths) + len(persisted.weaknesses)
        + len(persisted.opportunities) + len(persisted.threats)
    )
    if total >= 2:
        return persisted
    return _derive_fallback(session, founder_id)