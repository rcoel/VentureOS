"""Per-claim trust score.

    trust_score = base_confidence(source_type) × verification_multiplier(status)

Denormalized onto the Claim table at insert time so memo rendering is a
single JOIN, no runtime math.
"""

from __future__ import annotations

from ventureos_ui.scoring.constants import BASE_CONFIDENCE, VERIFICATION_MULTIPLIER


def compute_trust_score(source_type: str, verification_status: str) -> float:
    """Return a trust score in [0.0, 1.0]."""
    base = BASE_CONFIDENCE.get(source_type, 0.30)  # default: unknown source is low-trust
    mult = VERIFICATION_MULTIPLIER.get(verification_status, 0.50)
    return round(base * mult, 3)


def trust_badge(trust_score: float) -> str:
    """UI helper — return a single-char emoji badge for the trust tier."""
    from ventureos_ui.scoring.constants import TRUST_AMBER, TRUST_GREEN

    if trust_score >= TRUST_GREEN:
        return "🟢"
    if trust_score >= TRUST_AMBER:
        return "🟡"
    return "🔴"