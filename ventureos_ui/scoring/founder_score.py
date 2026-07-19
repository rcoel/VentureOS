"""Founder Score — 4-tier weighted sum with cold-start reweighting.

    FounderScore = w1·TrackRecord + w2·ExecutionSignal + w3·NarrativeQuality + w4·ConsistencyScore

Cold-start rule (the key innovation): when TrackRecord and/or ExecutionSignal
have ZERO underlying evidence, redistribute their weight into NarrativeQuality
+ ConsistencyScore. Never fill with zeros — that silently punishes cold-start
founders lacking a network.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ventureos_ui.models_orm import (
    Claim,
    Contradiction,
    EvidenceItem,
    FounderScore,
    ScoreHistory,
)
from ventureos_ui.scoring.constants import (
    ABSENCE_PREDICATES,
    BASE_CONFIDENCE,
    BASE_WEIGHTS,
    CONSISTENCY_START,
    CONTRADICTION_PENALTY,
    EXECUTION_SIGNAL_PREDICATES,
    NARRATIVE_QUALITY_PREDICATES,
    RECENCY_LAMBDA,
    TRACK_RECORD_PREDICATES,
    confidence_interval_width,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _recency_factor(fetched_at: datetime, now: datetime) -> float:
    """exp(-λ · days_since_observed). 6-month half-life."""
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - fetched_at).total_seconds() / 86400.0)
    return math.exp(-RECENCY_LAMBDA * days)


def _claim_weight(claim: Claim, evidence: EvidenceItem | None, now: datetime) -> float:
    """Per-claim weight: base_confidence(source) × verification × recency × self-conf."""
    base = BASE_CONFIDENCE.get(claim.source_type, 0.30)
    verif = {"verified": 1.0, "unverifiable": 0.5, "contradicted": 0.0}.get(
        claim.verification_status, 0.5
    )
    conf = max(0.1, min(1.0, claim.confidence))
    recency = _recency_factor(evidence.fetched_at, now) if evidence else 0.5
    return base * verif * recency * conf


def _component_score(
    predicates: set[str],
    claims: Iterable[Claim],
    evidence_by_id: dict[str, EvidenceItem],
    now: datetime,
) -> tuple[float, int]:
    """Return (score in 0-100, evidence_count).

    Score is an average trust-weighted contribution across claims whose
    predicate matches. We cap contribution at 100 so a single very strong
    claim can't dominate.
    """
    matched = [c for c in claims if c.predicate in predicates]
    if not matched:
        return 0.0, 0

    total = 0.0
    for c in matched:
        ev = evidence_by_id.get(c.source_evidence_id)
        w = _claim_weight(c, ev, now)
        total += w * 100.0  # each matched claim contributes up to 100·weight

    # Average across the matched claims, but let more claims lift the score
    # slightly (diminishing return via sqrt).
    avg = total / len(matched)
    boost = min(1.0 + math.log1p(len(matched)) * 0.15, 1.6)
    score = min(100.0, avg * boost)
    return score, len(matched)


def _consistency_score(contradictions: list[Contradiction]) -> float:
    return max(0.0, CONSISTENCY_START - CONTRADICTION_PENALTY * len(contradictions))


def _reweight_for_cold_start(
    tr_count: int, ex_count: int
) -> tuple[dict[str, float | None], bool]:
    """Cold-start reweighting.

    Returns (weights_dict, cold_start_applied). A weight of None means that
    component was dropped (no evidence) — memo/UI should render "no data"
    rather than "score = 0".
    """
    w = dict(BASE_WEIGHTS)  # {track_record, execution_signal, narrative_quality, consistency}
    cold_start = False

    if tr_count == 0 and ex_count == 0:
        # Full cold start — redistribute both into narrative + consistency
        released = w["track_record"] + w["execution_signal"]
        w["track_record"] = None
        w["execution_signal"] = None
        w["narrative_quality"] += released * (2 / 3)
        w["consistency"] += released * (1 / 3)
        cold_start = True
    elif tr_count == 0:
        released = w["track_record"]
        w["track_record"] = None
        w["narrative_quality"] += released
        cold_start = True
    elif ex_count == 0:
        released = w["execution_signal"]
        w["execution_signal"] = None
        w["narrative_quality"] += released
        cold_start = True

    return w, cold_start


# --------------------------------------------------------------------------- #
# Public entrypoint                                                           #
# --------------------------------------------------------------------------- #


def compute_and_persist(session: Session, founder_id: str) -> FounderScore:
    """Compute the Founder Score for one founder and upsert into founder_score
    + append a row to score_history.
    """
    now = datetime.now(timezone.utc)

    claims: list[Claim] = list(
        session.execute(select(Claim).where(Claim.founder_id == founder_id)).scalars()
    )
    evidence: list[EvidenceItem] = list(
        session.execute(
            select(EvidenceItem).where(EvidenceItem.founder_id == founder_id)
        ).scalars()
    )
    contradictions: list[Contradiction] = list(
        session.execute(
            select(Contradiction).where(Contradiction.founder_id == founder_id)
        ).scalars()
    )
    evidence_by_id = {e.id: e for e in evidence}

    # Filter out absence claims — they mark cold-start but shouldn't add to score
    positive_claims = [c for c in claims if c.predicate not in ABSENCE_PREDICATES]

    tr_score, tr_count = _component_score(
        TRACK_RECORD_PREDICATES, positive_claims, evidence_by_id, now
    )
    ex_score, ex_count = _component_score(
        EXECUTION_SIGNAL_PREDICATES, positive_claims, evidence_by_id, now
    )
    nq_score, _ = _component_score(
        NARRATIVE_QUALITY_PREDICATES, positive_claims, evidence_by_id, now
    )
    cs_score = _consistency_score(contradictions)

    weights, cold_start = _reweight_for_cold_start(tr_count, ex_count)

    # Weighted sum, skipping None components
    total = 0.0
    total_weight = 0.0
    if weights["track_record"] is not None:
        total += weights["track_record"] * tr_score
        total_weight += weights["track_record"]
    if weights["execution_signal"] is not None:
        total += weights["execution_signal"] * ex_score
        total_weight += weights["execution_signal"]
    total += weights["narrative_quality"] * nq_score
    total_weight += weights["narrative_quality"]
    total += weights["consistency"] * cs_score
    total_weight += weights["consistency"]

    founder_score = total / total_weight if total_weight else 0.0

    # Confidence interval
    ok_evidence = [e for e in evidence if e.status == "ok"]
    distinct_sources = len({e.source_type for e in ok_evidence})
    ci_width = confidence_interval_width(
        evidence_count=len(ok_evidence),
        source_diversity_ratio=distinct_sources / max(1, len(BASE_CONFIDENCE)),
    )

    # Upsert FounderScore row
    row = session.get(FounderScore, founder_id)
    if row is None:
        row = FounderScore(founder_id=founder_id)
        session.add(row)

    row.founder_score = round(founder_score, 2)
    row.confidence_interval_width = round(ci_width, 2)
    row.track_record_component = round(tr_score, 2) if weights["track_record"] is not None else None
    row.execution_signal_component = (
        round(ex_score, 2) if weights["execution_signal"] is not None else None
    )
    row.narrative_quality_component = round(nq_score, 2)
    row.consistency_component = round(cs_score, 2)
    row.weights_used = weights
    row.cold_start_applied = cold_start

    # Append to score history (never overwrite — this is what powers the chart)
    session.add(
        ScoreHistory(
            founder_id=founder_id,
            axis="overall",
            score=row.founder_score,
            computed_at=now,
        )
    )
    session.flush()
    return row