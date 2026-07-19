from sqlalchemy import (
    Column,
    String,
    Float,
    Text,
    DateTime,
    ForeignKey,
    JSON,
    Integer
)
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db.database import Base


def generate_uuid():
    return str(uuid.uuid4())


# ============================================================
# Founder
# ============================================================
# One row per PERSON, not per deal. Lets the Founder Score
# persist and update across multiple opportunities for the
# same human, instead of resetting per application.

class Founder(Base):
    __tablename__ = "founder"

    id = Column(String, primary_key=True, default=generate_uuid)

    name = Column(String, nullable=False)
    bio = Column(Text)

    links = Column(JSON)  # {"github": "...", "linkedin": "...", "twitter": "..."}

    source_type = Column(String)       # github / hn / devpost / arxiv / inbound / outbound
    channel_instance = Column(String)  # e.g. "MLH Fall 2026 Boston"

    founder_score = Column(Float, default=0)
    founder_score_confidence = Column(String)  # e.g. "62-78"

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    opportunities = relationship(
        "Opportunity",
        back_populates="founder",
        cascade="all, delete-orphan"
    )

    evidence_items = relationship(
        "EvidenceItem",
        back_populates="founder",
        cascade="all, delete-orphan"
    )

    score_history = relationship(
        "FounderScoreHistory",
        back_populates="founder",
        cascade="all, delete-orphan"
    )


# ============================================================
# Founder Score History
# ============================================================
# Persistent, append-only Founder Score timeline for a PERSON
# (separate from the per-opportunity ScoreHistory below, which
# tracks the 3 axis scores for a single deal).

class FounderScoreHistory(Base):
    __tablename__ = "founder_score_history"

    id = Column(String, primary_key=True, default=generate_uuid)

    founder_id = Column(
        String,
        ForeignKey("founder.id"),
        nullable=False
    )

    founder_score = Column(Float)
    confidence_range = Column(String)

    evidence_refs = Column(JSON)
    reasoning = Column(Text)

    recorded_at = Column(DateTime, default=datetime.utcnow)

    founder = relationship(
        "Founder",
        back_populates="score_history"
    )


# ============================================================
# Opportunity
# ============================================================

class Opportunity(Base):
    __tablename__ = "opportunity"

    id = Column(String, primary_key=True, default=generate_uuid)

    founder_id = Column(
        String,
        ForeignKey("founder.id"),
        nullable=True
    )

    company_name = Column(String, nullable=False)
    description = Column(Text)

    sector = Column(String)
    stage = Column(String)
    geography = Column(String)

    website = Column(String)
    linkedin_url = Column(String)
    github_url = Column(String)

    screen_status = Column(String)
    screen_reason = Column(Text)

    thesis_status = Column(String)
    thesis_reason = Column(Text)

    founder_score = Column(Float, default=0)
    market_score = Column(Float, default=0)
    product_score = Column(Float, default=0)
    confidence_score = Column(Float, default=0)

    memo_md = Column(Text)
    swot_summary = Column(Text)
    outreach_draft = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    founder = relationship(
        "Founder",
        back_populates="opportunities"
    )

    evidence_items = relationship(
        "EvidenceItem",
        back_populates="opportunity",
        cascade="all, delete-orphan"
    )

    claims = relationship(
        "Claim",
        back_populates="opportunity",
        cascade="all, delete-orphan"
    )

    contradictions = relationship(
        "Contradiction",
        back_populates="opportunity",
        cascade="all, delete-orphan"
    )

    score_history = relationship(
        "ScoreHistory",
        back_populates="opportunity",
        cascade="all, delete-orphan"
    )


# ============================================================
# Evidence
# ============================================================

class EvidenceItem(Base):
    __tablename__ = "evidence_item"

    id = Column(String, primary_key=True, default=generate_uuid)

    opportunity_id = Column(
        String,
        ForeignKey("opportunity.id"),
        nullable=True
    )

    founder_id = Column(
        String,
        ForeignKey("founder.id"),
        nullable=True
    )

    source_type = Column(String)
    source_url = Column(String)

    channel_provenance = Column(String)

    title = Column(String)

    content = Column(Text)

    trust_score = Column(Float)

    reasoning = Column(Text)

    evidence_refs = Column(JSON)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    opportunity = relationship(
        "Opportunity",
        back_populates="evidence_items"
    )

    founder = relationship(
        "Founder",
        back_populates="evidence_items"
    )


# ============================================================
# Claim
# ============================================================

class Claim(Base):
    __tablename__ = "claim"

    id = Column(String, primary_key=True, default=generate_uuid)

    opportunity_id = Column(
        String,
        ForeignKey("opportunity.id"),
        nullable=False
    )

    claim_type = Column(String)

    claim_value = Column(Text)

    confidence = Column(Float)

    reasoning = Column(Text)

    evidence_refs = Column(JSON)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    opportunity = relationship(
        "Opportunity",
        back_populates="claims"
    )


# ============================================================
# Contradiction
# ============================================================

class Contradiction(Base):
    __tablename__ = "contradiction"

    id = Column(String, primary_key=True, default=generate_uuid)

    opportunity_id = Column(
        String,
        ForeignKey("opportunity.id"),
        nullable=False
    )

    description = Column(Text)

    severity = Column(String)

    reasoning = Column(Text)

    evidence_refs = Column(JSON)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    opportunity = relationship(
        "Opportunity",
        back_populates="contradictions"
    )


# ============================================================
# Score History (per-opportunity, 3-axis)
# ============================================================

class ScoreHistory(Base):
    __tablename__ = "score_history"

    id = Column(String, primary_key=True, default=generate_uuid)

    opportunity_id = Column(
        String,
        ForeignKey("opportunity.id"),
        nullable=False
    )

    founder_score = Column(Float)

    market_score = Column(Float)

    product_score = Column(Float)

    confidence_score = Column(Float)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    opportunity = relationship(
        "Opportunity",
        back_populates="score_history"
    )


# ============================================================
# Thesis Config
# ============================================================

class ThesisConfig(Base):
    __tablename__ = "thesis_config"

    id = Column(String, primary_key=True, default=generate_uuid)

    sectors = Column(JSON)

    stage = Column(String)

    geography = Column(String)

    check_size_min = Column(Integer)

    check_size_max = Column(Integer)

    ownership_target = Column(Float)

    risk_appetite = Column(String)

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )