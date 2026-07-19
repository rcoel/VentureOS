"""3-axis screening — Founder / Market / Idea-vs-Market.

Each axis is stored independently and NEVER averaged into the founder score.
The brief is explicit: keep them side-by-side.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ventureos_ui.models_orm import (
    AxisScore,
    FounderScore,
    Founder,
    MarketResearch,
    ScoreHistory,
)
from ventureos_ui.scoring.constants import (
    AXIS_LABEL_BEAR_THRESHOLD,
    AXIS_LABEL_BULLISH_THRESHOLD,
    IDEA_STRONG_ASYMMETRY,
    MARKET_STANCE_TO_SCORE,
)
from ventureos_ui.scoring.trends import compute_trend


def _label_for(score: float) -> str:
    if score >= AXIS_LABEL_BULLISH_THRESHOLD:
        return "bullish"
    if score <= AXIS_LABEL_BEAR_THRESHOLD:
        return "bear"
    return "neutral"


# --------------------------------------------------------------------------- #
# Founder axis                                                                #
# --------------------------------------------------------------------------- #


def _founder_axis(session: Session, founder_id: str) -> tuple[float, str, list[str]]:
    """Score = 0.9 · FounderScore + 10 · (is_technical bonus).

    Reasoning: the FounderScore already blends the four tiers; the technical
    bump just marks whether we've seen credible engineering signal.
    """
    fs = session.get(FounderScore, founder_id)
    founder = session.get(Founder, founder_id)
    base = fs.founder_score if fs else 0.0
    attrs = founder.attributes if founder else {}
    is_tech = attrs.get("is_technical") if isinstance(attrs, dict) else None
    tech_bonus = 10.0 if is_tech is True else 0.0
    score = min(100.0, 0.9 * base + tech_bonus)

    evidence_refs: list[str] = []
    if fs:
        evidence_refs.append(f"founder_score={base}")
    if tech_bonus:
        evidence_refs.append("is_technical=true")
    return round(score, 2), _label_for(score), evidence_refs


# --------------------------------------------------------------------------- #
# Market axis                                                                 #
# --------------------------------------------------------------------------- #


def _market_axis(session: Session, founder_id: str) -> tuple[float, str, list[str]]:
    """Score derived from MarketResearch.stance ± competitor pressure."""
    mr = session.get(MarketResearch, founder_id)
    if mr is None:
        # No market research → neutral with wide interval implied elsewhere
        return 50.0, "neutral", ["no_market_research"]

    base = MARKET_STANCE_TO_SCORE.get(mr.stance, 50.0)
    competitor_count = len(mr.competitors or [])
    # More than 5 named competitors implies a crowded space — small penalty.
    crowd_penalty = min(15.0, max(0.0, (competitor_count - 5) * 2.0))
    score = max(0.0, min(100.0, base - crowd_penalty))

    refs = [f"stance={mr.stance}", f"competitors={competitor_count}"]
    if mr.market_size_estimate:
        refs.append(f"market_size_estimate present")
    return round(score, 2), _label_for(score), refs


# --------------------------------------------------------------------------- #
# Idea-vs-Market axis                                                         #
# --------------------------------------------------------------------------- #


def _idea_vs_market_axis(
    founder_axis: float, market_axis: float
) -> tuple[float, str, list[str]]:
    """Explicitly models 'team over idea' vs 'market over team' vs 'balanced'.

    From your spec: "does this team's Founder Score justify backing even a
    mediocre idea axis?"
    """
    gap = founder_axis - market_axis
    if gap >= IDEA_STRONG_ASYMMETRY:
        # Team is much stronger than the market signal
        score = min(70.0, founder_axis - 5.0)
        return round(score, 2), "team over idea", ["founder_axis >> market_axis"]
    if -gap >= IDEA_STRONG_ASYMMETRY:
        # Market signal much stronger than the team
        score = min(70.0, market_axis - 5.0)
        return round(score, 2), "market over team", ["market_axis >> founder_axis"]
    score = 0.5 * (founder_axis + market_axis)
    return round(score, 2), "balanced", ["founder_axis ~= market_axis"]


# --------------------------------------------------------------------------- #
# Public entrypoint                                                           #
# --------------------------------------------------------------------------- #


def _upsert_axis(
    session: Session,
    founder_id: str,
    axis: str,
    score: float,
    label: str,
    evidence_refs: list[str],
    reasoning: str,
) -> None:
    trend = compute_trend(session, founder_id, axis)

    row = session.get(AxisScore, {"founder_id": founder_id, "axis": axis})
    if row is None:
        row = AxisScore(founder_id=founder_id, axis=axis)
        session.add(row)
    row.score = score
    row.label = label
    row.trend = trend
    row.evidence_refs = evidence_refs
    row.reasoning = reasoning

    # Append to history — powers trend arrows next reload
    session.add(ScoreHistory(founder_id=founder_id, axis=axis, score=score))


def compute_and_persist(session: Session, founder_id: str) -> dict[str, AxisScore]:
    """Compute and persist all three axes. Returns them as a dict."""
    f_score, f_label, f_refs = _founder_axis(session, founder_id)
    m_score, m_label, m_refs = _market_axis(session, founder_id)
    i_score, i_label, i_refs = _idea_vs_market_axis(f_score, m_score)

    _upsert_axis(
        session, founder_id, "founder", f_score, f_label, f_refs,
        f"Founder axis {f_label} based on FounderScore + technical signal.",
    )
    _upsert_axis(
        session, founder_id, "market", m_score, m_label, m_refs,
        f"Market axis {m_label} based on stance and competitor pressure.",
    )
    _upsert_axis(
        session, founder_id, "idea_vs_market", i_score, i_label, i_refs,
        f"Idea-vs-Market: {i_label} (founder={f_score}, market={m_score}).",
    )

    session.flush()
    return {
        axis: session.get(AxisScore, {"founder_id": founder_id, "axis": axis})
        for axis in ("founder", "market", "idea_vs_market")
    }