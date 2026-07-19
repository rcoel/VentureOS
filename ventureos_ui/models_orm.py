"""SQLAlchemy 2.0 ORM tables — mirror the Pydantic contract in ventureos.models.

Design rules:
1. Every table storing pipeline output has JSON columns for the raw payloads,
   so we never lose the full audit trail.
2. Composite/derived scores (FounderScore, AxisScore) are separate tables
   so a recompute is a single UPDATE, not a founder-wide rewrite.
3. ScoreHistory is append-only — every recompute writes a new row. This is
   the source of truth for trend calculations.
4. `computed_at` on every score row lets us reconstruct "score over time"
   for the demo chart.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Founder — root aggregate                                                     #
# --------------------------------------------------------------------------- #


class Founder(Base):
    __tablename__ = "founder"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    founder_name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    is_outbound: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(40), default="inbound")
    reference_url: Mapped[str | None] = mapped_column(Text)

    # Derived attributes (from FounderAttributes)
    location: Mapped[str | None] = mapped_column(String(200))
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    intake: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    devpost_extras: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # Outreach + screening
    outreach_draft: Mapped[str | None] = mapped_column(Text)
    screen_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    screen_reason: Mapped[str] = mapped_column(Text, default="")

    # Agent trace — captured from the pipeline's final_state
    reasoning_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    trace: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Preliminary score (internal to pipeline — never displayed on its own)
    preliminary_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Metadata
    first_scored_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships (mostly read-side convenience — writes use direct upserts)
    evidence: Mapped[list["EvidenceItem"]] = relationship(back_populates="founder", cascade="all, delete-orphan")
    claims: Mapped[list["Claim"]] = relationship(back_populates="founder", cascade="all, delete-orphan")
    contradictions: Mapped[list["Contradiction"]] = relationship(back_populates="founder", cascade="all, delete-orphan")


# --------------------------------------------------------------------------- #
# Evidence — one row per API call result / synthesized deck                   #
# --------------------------------------------------------------------------- #


class EvidenceItem(Base):
    __tablename__ = "evidence_item"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_content: Mapped[dict[str, Any]] = mapped_column(JSON)
    query_used: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    status: Mapped[str] = mapped_column(String(20), default="ok")

    founder: Mapped["Founder"] = relationship(back_populates="evidence")


# --------------------------------------------------------------------------- #
# Claim — extracted fact + verification + trust score                         #
# --------------------------------------------------------------------------- #


class Claim(Base):
    __tablename__ = "claim"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"), index=True)
    source_evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence_item.id"), index=True)

    text: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(String(20))
    predicate: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    source_type: Mapped[str] = mapped_column(String(40))

    verification_status: Mapped[str] = mapped_column(String(20), default="unverifiable")
    trust_score: Mapped[float] = mapped_column(Float, default=0.0)

    founder: Mapped["Founder"] = relationship(back_populates="claims")

    __table_args__ = (
        Index("ix_claim_founder_predicate", "founder_id", "predicate"),
    )


# --------------------------------------------------------------------------- #
# Contradiction — flagged mismatch between two claims                         #
# --------------------------------------------------------------------------- #


class Contradiction(Base):
    __tablename__ = "contradiction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"), index=True)
    claim_id_a: Mapped[str] = mapped_column(ForeignKey("claim.id"))
    claim_id_b: Mapped[str] = mapped_column(ForeignKey("claim.id"))
    description: Mapped[str] = mapped_column(Text)
    predicate: Mapped[str] = mapped_column(String(80))

    founder: Mapped["Founder"] = relationship(back_populates="contradictions")


# --------------------------------------------------------------------------- #
# MarketResearch — 1:1 with Founder                                           #
# --------------------------------------------------------------------------- #


class MarketResearch(Base):
    __tablename__ = "market_research"

    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"), primary_key=True)
    competitors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    market_size_estimate: Mapped[str | None] = mapped_column(Text)
    stance: Mapped[str] = mapped_column(String(20), default="neutral")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)


# --------------------------------------------------------------------------- #
# SWOTEntry — one row per SWOT bullet, citation-backed                        #
# --------------------------------------------------------------------------- #


class SWOTEntry(Base):
    __tablename__ = "swot_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"), index=True)
    quadrant: Mapped[str] = mapped_column(String(20))  # strength|weakness|opportunity|threat
    text: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (
        Index("ix_swot_founder_quadrant", "founder_id", "quadrant"),
    )


# --------------------------------------------------------------------------- #
# FounderScore — current computed 4-tier score                                #
# --------------------------------------------------------------------------- #


class FounderScore(Base):
    __tablename__ = "founder_score"

    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"), primary_key=True)

    founder_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_interval_width: Mapped[float] = mapped_column(Float, default=40.0)

    # Component scores (null if not applicable — cold start marker)
    track_record_component: Mapped[float | None] = mapped_column(Float)
    execution_signal_component: Mapped[float | None] = mapped_column(Float)
    narrative_quality_component: Mapped[float] = mapped_column(Float, default=0.0)
    consistency_component: Mapped[float] = mapped_column(Float, default=100.0)

    weights_used: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cold_start_applied: Mapped[bool] = mapped_column(Boolean, default=False)

    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


# --------------------------------------------------------------------------- #
# AxisScore — 3-axis screening (Founder / Market / Idea-vs-Market)             #
# --------------------------------------------------------------------------- #


class AxisScore(Base):
    __tablename__ = "axis_score"

    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"))
    axis: Mapped[str] = mapped_column(String(40))  # 'founder' | 'market' | 'idea_vs_market'

    score: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str] = mapped_column(String(40), default="neutral")
    trend: Mapped[str] = mapped_column(String(20), default="stable")
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("founder_id", "axis", name="pk_axis_score"),
    )


# --------------------------------------------------------------------------- #
# ScoreHistory — append-only, source of truth for trends                      #
# --------------------------------------------------------------------------- #


class ScoreHistory(Base):
    __tablename__ = "score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"), index=True)
    axis: Mapped[str] = mapped_column(String(40))  # 'overall' | 'founder' | 'market' | 'idea_vs_market'
    score: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_score_history_founder_axis", "founder_id", "axis"),
    )


# --------------------------------------------------------------------------- #
# ThesisConfig — singleton (id = 'current')                                   #
# --------------------------------------------------------------------------- #


class ThesisConfig(Base):
    __tablename__ = "thesis_config"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default="current")
    sectors: Mapped[list[str]] = mapped_column(JSON, default=list)
    stage: Mapped[str] = mapped_column(String(40), default="pre-seed")
    geography: Mapped[list[str]] = mapped_column(JSON, default=list)
    check_size_min: Mapped[int] = mapped_column(Integer, default=25_000)
    check_size_max: Mapped[int] = mapped_column(Integer, default=150_000)
    ownership_target: Mapped[float] = mapped_column(Float, default=0.05)
    risk_appetite: Mapped[str] = mapped_column(String(20), default="high")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


# --------------------------------------------------------------------------- #
# ThesisFit — computed thesis fit per founder                                 #
# --------------------------------------------------------------------------- #


class ThesisFit(Base):
    __tablename__ = "thesis_fit"

    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"), primary_key=True)
    thesis_fit: Mapped[str] = mapped_column(String(20), default="in_thesis")
    reason: Mapped[str] = mapped_column(Text, default="")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


# --------------------------------------------------------------------------- #
# Memo — cached rendered Markdown                                             #
# --------------------------------------------------------------------------- #


class Memo(Base):
    __tablename__ = "memo"

    founder_id: Mapped[str] = mapped_column(ForeignKey("founder.id"), primary_key=True)
    markdown: Mapped[str] = mapped_column(Text, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    prompt_version: Mapped[str] = mapped_column(String(40), default="v1")