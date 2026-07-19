"""Trend calculation — compare current axis score to the previous one in
`score_history` to determine 'improving' / 'declining' / 'stable'.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ventureos_ui.models_orm import ScoreHistory
from ventureos_ui.scoring.constants import TREND_STABLE_DELTA

Trend = Literal["improving", "declining", "stable"]


def latest_two_scores(
    session: Session, founder_id: str, axis: str
) -> tuple[float | None, float | None]:
    """Return (previous_score, current_score) for the given axis. Either or
    both may be None if history is empty."""
    rows = list(
        session.execute(
            select(ScoreHistory.score)
            .where(ScoreHistory.founder_id == founder_id, ScoreHistory.axis == axis)
            .order_by(ScoreHistory.computed_at.desc())
            .limit(2)
        ).scalars()
    )
    current = rows[0] if len(rows) >= 1 else None
    previous = rows[1] if len(rows) >= 2 else None
    return previous, current


def compute_trend(session: Session, founder_id: str, axis: str) -> Trend:
    previous, current = latest_two_scores(session, founder_id, axis)
    if previous is None or current is None:
        return "stable"
    delta = current - previous
    if abs(delta) <= TREND_STABLE_DELTA:
        return "stable"
    return "improving" if delta > 0 else "declining"


def trend_arrow(trend: Trend) -> str:
    return {"improving": "↑", "declining": "↓", "stable": "→"}[trend]