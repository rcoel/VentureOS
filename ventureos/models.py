"""Frozen data contract — shared between the pipeline (Person A) and scoring/UI (Person B).

Any change here MUST be communicated. Person B's SQLAlchemy models and scoring code
depend on the exact shape of these types.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Evidence — one row per API call result (or per document/deck section)      #
# --------------------------------------------------------------------------- #

SourceType = Literal[
    "github",
    "hn",
    "semantic_scholar",
    "tavily",
    "serpapi",
    "deck",
]


class EvidenceItem(BaseModel):
    """Raw evidence pulled from an external source, preserved verbatim."""

    id: str = Field(default_factory=lambda: f"ev_{uuid4().hex[:12]}")
    founder_name: str
    source_type: SourceType
    source_url: str | None = None
    raw_content: dict[str, Any]
    query_used: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["ok", "not_found", "error"] = "ok"


# --------------------------------------------------------------------------- #
# Claim — structured fact extracted from an EvidenceItem                     #
# --------------------------------------------------------------------------- #


class Claim(BaseModel):
    """A structured fact extracted from evidence.

    Every claim MUST carry `source_evidence_id` — this is the traceability spine.
    """

    id: str = Field(default_factory=lambda: f"cl_{uuid4().hex[:12]}")
    founder_name: str
    text: str
    subject: Literal["founder", "product", "market", "company"]
    predicate: str  # free-form but conventional (e.g. "prior_role", "funding_raised")
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_evidence_id: str
    source_type: SourceType


class ClaimList(BaseModel):
    """Wrapper for OpenAI structured outputs — a list of claims from one evidence item."""

    claims: list[Claim] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Contradiction — flagged mismatch between two claims                        #
# --------------------------------------------------------------------------- #


class Contradiction(BaseModel):
    claim_id_a: str
    claim_id_b: str
    description: str
    predicate: str


# --------------------------------------------------------------------------- #
# Founder attributes — typed rollup for multi-attribute queries              #
# --------------------------------------------------------------------------- #


class FounderAttributes(BaseModel):
    """Typed summary of a founder's claims — enables compound queries.

    Every field is nullable — unknown fields must render as `[Not Disclosed]`
    downstream, not defaulted to false/zero (which would silently punish
    cold-start founders).
    """

    is_technical: bool | None = None
    location: str | None = None
    categories: list[str] = Field(default_factory=list)
    customer_segment: Literal["consumer", "smb", "enterprise", "developer"] | None = None
    prior_vc_backing: bool | None = None
    accelerator_tier: Literal["yc", "techstars", "other", "none"] | None = None
    prior_exits: int | None = None
    years_experience: int | None = None
    is_researcher: bool = False
    h_index: int | None = None


# --------------------------------------------------------------------------- #
# Market research                                                            #
# --------------------------------------------------------------------------- #


class Competitor(BaseModel):
    name: str
    url: str | None = None
    one_liner: str


class MarketResearch(BaseModel):
    competitors: list[Competitor] = Field(default_factory=list)
    market_size_estimate: str | None = None  # null → "[Not Disclosed]" in UI
    stance: Literal["bullish", "neutral", "bear"]
    reasoning: str
    evidence_refs: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Intake summary                                                              #
# --------------------------------------------------------------------------- #


class IntakeSummary(BaseModel):
    github_handle_hints: list[str] = Field(default_factory=list)
    research_domain: str | None = None
    is_research_founder: bool = False
    category_labels: list[str] = Field(default_factory=list)
    product_urls: list[str] = Field(default_factory=list)
    location_hint: str | None = None


# --------------------------------------------------------------------------- #
# Screening decision                                                          #
# --------------------------------------------------------------------------- #


class ScreeningDecision(BaseModel):
    status: Literal["PASS", "FAIL"]
    reason: str


# --------------------------------------------------------------------------- #
# Verification result (per predicate group)                                  #
# --------------------------------------------------------------------------- #


class ContradictionPair(BaseModel):
    a: str  # claim id
    b: str  # claim id
    description: str


class VerificationResult(BaseModel):
    verified_ids: list[str] = Field(default_factory=list)
    unverifiable_ids: list[str] = Field(default_factory=list)
    contradictions: list[ContradictionPair] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Query filter — for the natural-language query bar                          #
# --------------------------------------------------------------------------- #


class OutreachDraft(BaseModel):
    """Wrapper for the activation node's structured output."""

    outreach_draft: str


# --------------------------------------------------------------------------- #
# Devpost winner extraction (outbound discovery)                              #
# --------------------------------------------------------------------------- #


class DevpostWinner(BaseModel):
    """One winning project pulled from a Devpost hackathon page.

    `project_name` is what we treat as the "company" name downstream.
    `founder_name` is the primary listed member, falling back to team name.
    `description` is passed as the application_text to the pipeline.
    """

    project_name: str
    founder_name: str
    team_name: str | None = None
    description: str
    prize_or_placement: str | None = None
    github_url: str | None = None
    project_url: str | None = None


class DevpostWinnerList(BaseModel):
    """Wrapper for OpenAI structured output — list of winners from one page."""

    hackathon_name: str | None = None
    winners: list[DevpostWinner] = Field(default_factory=list)


class HackathonRef(BaseModel):
    """One hackathon listed on the Devpost hackathons index page."""

    name: str
    url: str  # e.g. https://gitlab.devpost.com/ — the hackathon's landing page
    status: Literal["ended", "in_progress", "upcoming", "unknown"] = "unknown"


class HackathonList(BaseModel):
    hackathons: list[HackathonRef] = Field(default_factory=list)


class ProjectRef(BaseModel):
    """A project link found on a hackathon's project-gallery page."""

    project_name: str
    project_url: str  # canonical devpost.com/software/... URL
    prize_or_placement: str | None = None


class ProjectRefList(BaseModel):
    projects: list[ProjectRef] = Field(default_factory=list)


class QueryFilter(BaseModel):
    """Parsed natural-language query into typed filter fields."""

    is_technical: bool | None = None
    location_contains: str | None = None
    categories_any: list[str] = Field(default_factory=list)
    customer_segment: Literal["consumer", "smb", "enterprise", "developer"] | None = None
    prior_vc_backing: bool | None = None
    accelerator_tier: Literal["yc", "techstars", "other", "none"] | None = None
    min_prior_exits: int | None = None
    is_researcher: bool | None = None
    min_h_index: int | None = None