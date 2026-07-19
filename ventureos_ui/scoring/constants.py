"""Scoring constants — base weights, source-trust table, decay, thresholds.

Change these in one place and every scoring component picks it up.
"""

from __future__ import annotations

import math

# --------------------------------------------------------------------------- #
# Trust score constants                                                       #
# --------------------------------------------------------------------------- #

# Base confidence in a source's reliability. Higher = we trust it more.
BASE_CONFIDENCE: dict[str, float] = {
    "github": 0.90,
    "semantic_scholar": 0.90,
    "hn": 0.60,
    "tavily": 0.50,
    "serpapi": 0.50,
    "deck": 0.40,
}

# Multiplier applied based on cross-source verification outcome.
VERIFICATION_MULTIPLIER: dict[str, float] = {
    "verified": 1.00,
    "unverifiable": 0.50,
    "contradicted": 0.00,  # flagged but not counted toward positive signal
}

# Threshold for the memo's trust badge colour.
TRUST_GREEN = 0.70
TRUST_AMBER = 0.40


# --------------------------------------------------------------------------- #
# Founder score — 4-tier base weights                                          #
# --------------------------------------------------------------------------- #

BASE_WEIGHTS = {
    "track_record": 0.35,
    "execution_signal": 0.30,
    "narrative_quality": 0.20,
    "consistency": 0.15,
}

# Recency decay lambda — half-life of 180 days.
RECENCY_LAMBDA = math.log(2) / 180.0

# Contradiction penalty for the consistency component.
CONSISTENCY_START = 100.0
CONTRADICTION_PENALTY = 20.0  # points off per contradiction

# --------------------------------------------------------------------------- #
# Predicate → component mapping                                               #
# --------------------------------------------------------------------------- #

# Which predicates feed each of the 4 tiers.
TRACK_RECORD_PREDICATES = {
    "prior_role",
    "prior_company",
    "prior_exit",
    "h_index",
    "paper_count",
    "citation_count",
    "top_tier_affiliation",
    "notable_venue",
    "accelerator_tier",
    "yc_batch",
    "education",
}

EXECUTION_SIGNAL_PREDICATES = {
    "shipped_product",
    "execution_velocity",
    "launch_reception",
    "launch_praise",
    "hackathon_win",
    "product_launch_date",
    "ph_launch",
    "ph_upvotes",
    "open_source_reach",
    "tech_stack",
    "active_research",
}

NARRATIVE_QUALITY_PREDICATES = {
    "product_description",
    "problem_statement",
    "customer_segment",
    "market_category",
    "founder_hn_activity",
    "press_mention",
}

# Absence predicates — these DO NOT contribute to positive score, only used
# to detect cold-start.
ABSENCE_PREDICATES = {
    "github_absence",
    "hn_absence",
    "s2_absence",
    "s2_no_match",
    "site_absence",
    "narrative_absence",
    "identity_mismatch",
}


# --------------------------------------------------------------------------- #
# Confidence interval                                                         #
# --------------------------------------------------------------------------- #

def confidence_interval_width(evidence_count: int, source_diversity_ratio: float) -> float:
    """Width in points (± of the score). Fewer sources → wider interval.

    - `evidence_count`: number of 'ok' EvidenceItems.
    - `source_diversity_ratio`: distinct_source_types / 6 (we have 6 source types).
    """
    return 40.0 * math.exp(-0.3 * evidence_count) * (1.0 - 0.5 * source_diversity_ratio)


# --------------------------------------------------------------------------- #
# 3-axis screening — thresholds                                               #
# --------------------------------------------------------------------------- #

AXIS_LABEL_BULLISH_THRESHOLD = 70
AXIS_LABEL_BEAR_THRESHOLD = 30

MARKET_STANCE_TO_SCORE = {
    "bullish": 75.0,
    "neutral": 50.0,
    "bear": 25.0,
}

# Idea-vs-Market: when to declare "team over idea" or "market over team"
IDEA_STRONG_ASYMMETRY = 35  # min points gap between founder and market axis


# --------------------------------------------------------------------------- #
# Trend detection                                                             #
# --------------------------------------------------------------------------- #

TREND_STABLE_DELTA = 3.0  # ≤3 point change = stable