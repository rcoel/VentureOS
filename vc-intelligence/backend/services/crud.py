"""
CRUD helpers for every table. Plain functions, not a class -- call
these directly from LangGraph nodes (Person A) or Streamlit callbacks
(Person B) without needing FastAPI in between.

Every function opens its own short-lived session via SessionLocal()
and commits before returning, so callers don't need to manage
sessions themselves.
"""

from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db.database import SessionLocal
from models.models import (
    Founder,
    FounderScoreHistory,
    Opportunity,
    EvidenceItem,
    Claim,
    Contradiction,
    ScoreHistory,
    ThesisConfig,
)


# ============================================================
# Founder
# ============================================================

def create_founder(**kwargs) -> Founder:
    with SessionLocal() as db:
        founder = Founder(**kwargs)
        db.add(founder)
        db.commit()
        db.refresh(founder)
        return founder


def get_founder(founder_id: str) -> Founder | None:
    with SessionLocal() as db:
        return db.query(Founder).filter(Founder.id == founder_id).first()


def get_founder_by_name(name: str) -> Founder | None:
    """Cheap dedup check before creating a new founder row."""
    with SessionLocal() as db:
        return db.query(Founder).filter(Founder.name == name).first()


def get_all_founders() -> list[Founder]:
    with SessionLocal() as db:
        return db.query(Founder).all()


def update_founder(founder_id: str, **kwargs) -> Founder | None:
    with SessionLocal() as db:
        founder = db.query(Founder).filter(Founder.id == founder_id).first()
        if not founder:
            return None
        for key, value in kwargs.items():
            setattr(founder, key, value)
        db.commit()
        db.refresh(founder)
        return founder


def delete_founder(founder_id: str) -> bool:
    with SessionLocal() as db:
        founder = db.query(Founder).filter(Founder.id == founder_id).first()
        if not founder:
            return False
        db.delete(founder)
        db.commit()
        return True


def record_founder_score(
    founder_id: str,
    founder_score: float,
    confidence_range: str,
    evidence_refs: list,
    reasoning: str,
) -> FounderScoreHistory:
    """
    Append-only write -- never update an existing row for this.
    This is what powers the score-over-time chart. Also updates
    the current-value fields on Founder itself for quick reads.
    """
    with SessionLocal() as db:
        entry = FounderScoreHistory(
            founder_id=founder_id,
            founder_score=founder_score,
            confidence_range=confidence_range,
            evidence_refs=evidence_refs,
            reasoning=reasoning,
        )
        db.add(entry)

        founder = db.query(Founder).filter(Founder.id == founder_id).first()
        if founder:
            founder.founder_score = founder_score
            founder.founder_score_confidence = confidence_range

        db.commit()
        db.refresh(entry)
        return entry


def get_founder_score_history(founder_id: str) -> list[FounderScoreHistory]:
    with SessionLocal() as db:
        return (
            db.query(FounderScoreHistory)
            .filter(FounderScoreHistory.founder_id == founder_id)
            .order_by(FounderScoreHistory.recorded_at.asc())
            .all()
        )


# ============================================================
# Opportunity
# ============================================================

def create_opportunity(**kwargs) -> Opportunity:
    with SessionLocal() as db:
        opp = Opportunity(**kwargs)
        db.add(opp)
        db.commit()
        db.refresh(opp)
        return opp


def get_opportunity(opportunity_id: str) -> Opportunity | None:
    with SessionLocal() as db:
        return db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()


def get_all_opportunities() -> list[Opportunity]:
    with SessionLocal() as db:
        return db.query(Opportunity).all()


def get_opportunities_by_screen_status(status: str) -> list[Opportunity]:
    with SessionLocal() as db:
        return (
            db.query(Opportunity)
            .filter(Opportunity.screen_status == status)
            .all()
        )


def get_opportunities_by_thesis_status(status: str) -> list[Opportunity]:
    with SessionLocal() as db:
        return (
            db.query(Opportunity)
            .filter(Opportunity.thesis_status == status)
            .all()
        )


def update_opportunity(opportunity_id: str, **kwargs) -> Opportunity | None:
    with SessionLocal() as db:
        opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
        if not opp:
            return None
        for key, value in kwargs.items():
            setattr(opp, key, value)
        db.commit()
        db.refresh(opp)
        return opp


def delete_opportunity(opportunity_id: str) -> bool:
    with SessionLocal() as db:
        opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
        if not opp:
            return False
        db.delete(opp)
        db.commit()
        return True


# ============================================================
# Evidence
# ============================================================

def create_evidence_item(**kwargs) -> EvidenceItem:
    with SessionLocal() as db:
        item = EvidenceItem(**kwargs)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item


def get_evidence_for_opportunity(opportunity_id: str) -> list[EvidenceItem]:
    with SessionLocal() as db:
        return (
            db.query(EvidenceItem)
            .filter(EvidenceItem.opportunity_id == opportunity_id)
            .all()
        )


def get_evidence_for_founder(founder_id: str) -> list[EvidenceItem]:
    with SessionLocal() as db:
        return (
            db.query(EvidenceItem)
            .filter(EvidenceItem.founder_id == founder_id)
            .all()
        )


def get_evidence_by_source_type(source_type: str) -> list[EvidenceItem]:
    """Useful for the cache-check-first pattern before hitting an external API."""
    with SessionLocal() as db:
        return (
            db.query(EvidenceItem)
            .filter(EvidenceItem.source_type == source_type)
            .all()
        )


# ============================================================
# Claim
# ============================================================

def create_claim(**kwargs) -> Claim:
    with SessionLocal() as db:
        claim = Claim(**kwargs)
        db.add(claim)
        db.commit()
        db.refresh(claim)
        return claim


def get_claims_for_opportunity(opportunity_id: str) -> list[Claim]:
    with SessionLocal() as db:
        return db.query(Claim).filter(Claim.opportunity_id == opportunity_id).all()


# ============================================================
# Contradiction
# ============================================================

def create_contradiction(**kwargs) -> Contradiction:
    with SessionLocal() as db:
        contradiction = Contradiction(**kwargs)
        db.add(contradiction)
        db.commit()
        db.refresh(contradiction)
        return contradiction


def get_contradictions_for_opportunity(opportunity_id: str) -> list[Contradiction]:
    with SessionLocal() as db:
        return (
            db.query(Contradiction)
            .filter(Contradiction.opportunity_id == opportunity_id)
            .all()
        )


# ============================================================
# Score History (per-opportunity, 3-axis)
# ============================================================

def record_score_history(
    opportunity_id: str,
    founder_score: float,
    market_score: float,
    product_score: float,
    confidence_score: float,
) -> ScoreHistory:
    """
    Append-only. Also updates the current-value fields on
    Opportunity so a fresh read doesn't need to look at history.
    """
    with SessionLocal() as db:
        entry = ScoreHistory(
            opportunity_id=opportunity_id,
            founder_score=founder_score,
            market_score=market_score,
            product_score=product_score,
            confidence_score=confidence_score,
        )
        db.add(entry)

        opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
        if opp:
            opp.founder_score = founder_score
            opp.market_score = market_score
            opp.product_score = product_score
            opp.confidence_score = confidence_score

        db.commit()
        db.refresh(entry)
        return entry


def get_score_history(opportunity_id: str) -> list[ScoreHistory]:
    with SessionLocal() as db:
        return (
            db.query(ScoreHistory)
            .filter(ScoreHistory.opportunity_id == opportunity_id)
            .order_by(ScoreHistory.created_at.asc())
            .all()
        )


def get_score_trend(opportunity_id: str, axis: str) -> str:
    """
    axis: 'founder_score' | 'market_score' | 'product_score'
    Returns 'improving' | 'declining' | 'stable' | 'insufficient_data'
    by comparing the two most recent history entries.
    """
    history = get_score_history(opportunity_id)
    if len(history) < 2:
        return "insufficient_data"
    current = getattr(history[-1], axis)
    previous = getattr(history[-2], axis)
    if current > previous:
        return "improving"
    elif current < previous:
        return "declining"
    return "stable"


# ============================================================
# Thesis Config
# ============================================================
# Single-row table by convention -- always read/write the first
# (and only) row rather than tracking an id externally.

def get_thesis_config() -> ThesisConfig | None:
    with SessionLocal() as db:
        return db.query(ThesisConfig).first()


def upsert_thesis_config(**kwargs) -> ThesisConfig:
    """
    Creates the single config row if it doesn't exist yet,
    otherwise updates it in place. Call this directly from the
    Streamlit sidebar on every input change -- no save button needed.
    """
    with SessionLocal() as db:
        config = db.query(ThesisConfig).first()
        if config is None:
            config = ThesisConfig(**kwargs)
            db.add(config)
        else:
            for key, value in kwargs.items():
                setattr(config, key, value)
        db.commit()
        db.refresh(config)
        return config