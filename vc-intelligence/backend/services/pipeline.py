"""
pipeline.py -- simulated version of Person A's real LangGraph pipeline.

Two entry points the dashboard calls directly:

  seed_initial_data()
      Simulates the outbound sourcing scan (GitHub/HN/Devpost/etc).
      Creates Founder + Opportunity rows with only the "raw intake"
      fields filled in -- no scores yet, screen_status="pending".

  run_analysis(opportunity_id)
      Simulates: Screening -> Extraction -> Verification -> Scoring ->
      Market -> SWOT -> Memo, all in one function for now. Writes
      scores/status/swot/memo back to the Opportunity row.

Plus four read-only mock generators the tabs call to render detail
views (Founder / Market / SWOT / Memo). These do NOT write to the DB
-- they compute a fresh mock "LLM-style" payload from the opportunity's
already-analyzed fields each time the tab renders. Swap the body of
each for a real LLM/LangGraph call later; the return dict shape is
the contract the UI is built against, so app.py won't need to change.
"""

import random
import time

from services import crud


# ============================================================
# Simulated sourcing -- stand-in for the real outbound scan
# ============================================================

SIMULATED_SOURCED_FOUNDERS = [
    {
        "name": "Priya Nair",
        "bio": "Solo builder, Show HN post 3 weeks ago, small but real user base.",
        "source_type": "hn",
        "company_name": "Ledgerly",
        "description": "AI bookkeeping assistant for freelancers.",
        "sector": "Fintech",
        "stage": "Pre-seed",
        "geography": "India",
        "github_url": "https://github.com/example/ledgerly",
    },
    {
        "name": "Tom Weisz",
        "bio": "Recent MLH hackathon winner, actively shipping since.",
        "source_type": "hackathon_win",
        "company_name": "PatchPilot",
        "description": "Automated dependency-upgrade PRs for old codebases.",
        "sector": "Devtools",
        "stage": "Pre-seed",
        "geography": "US",
        "github_url": "https://github.com/example/patchpilot",
    },
    {
        "name": "Amara Diallo",
        "bio": "No public GitHub, no accelerator -- deck-only cold start.",
        "source_type": "inbound",
        "company_name": "Clinly",
        "description": "Scheduling + intake automation for small clinics.",
        "sector": "Healthtech",
        "stage": "Pre-seed",
        "geography": "EU",
        "github_url": None,
    },
    {
        "name": "Jordan Blake",
        "bio": "Cold inbound, one-line pitch, no other detail supplied yet.",
        "source_type": "inbound",
        "company_name": "Vantix",
        "description": "TBD",
        "sector": None,
        "stage": "Pre-seed",
        "geography": None,
        "github_url": None,
    },
]


def seed_initial_data():
    """
    Idempotent-ish: skips a founder if one with the same name already
    exists, so clicking this more than once during the demo doesn't
    duplicate rows.
    """
    created = []
    for entry in SIMULATED_SOURCED_FOUNDERS:
        existing = crud.get_founder_by_name(entry["name"])
        if existing:
            continue

        founder = crud.create_founder(
            name=entry["name"],
            bio=entry["bio"],
            links={"github": entry["github_url"]} if entry["github_url"] else {},
            source_type=entry["source_type"],
        )

        opportunity = crud.create_opportunity(
            founder_id=founder.id,
            company_name=entry["company_name"],
            description=entry["description"],
            sector=entry["sector"],
            stage=entry["stage"],
            geography=entry["geography"],
            github_url=entry["github_url"],
            screen_status="pending",
        )

        crud.create_evidence_item(
            opportunity_id=opportunity.id,
            founder_id=founder.id,
            source_type=entry["source_type"],
            source_url=entry["github_url"] or "N/A",
            title=f"Initial sourcing signal for {entry['name']}",
            content=entry["bio"],
            trust_score=0.6,
            reasoning="Raw sourcing hit, not yet verified.",
        )

        created.append(opportunity)

    return created


# ============================================================
# Simulated analysis -- stand-in for the real LLM/LangGraph call
# ============================================================

MIN_DESCRIPTION_LENGTH = 15  # chars -- "TBD" etc. won't clear this


def _run_screening(opportunity) -> tuple[str, str]:
    """Real screening pass -- checks field shape (length/presence),
    not any specific demo founder's name, so it behaves the same way
    once real sourced rows arrive. Returns (screen_status, screen_reason).
    """
    description = (opportunity.description or "").strip()
    company_name = (opportunity.company_name or "").strip()

    if not company_name:
        return "failed", "No company name provided."

    if not description or len(description) < MIN_DESCRIPTION_LENGTH:
        return "failed", (
            f"Description too thin to evaluate "
            f"({len(description)} chars, need at least {MIN_DESCRIPTION_LENGTH})."
        )

    if not opportunity.sector:
        return "failed", "No sector provided -- can't be categorized."

    return "passed", "Coherent product description and legible category."


def run_analysis(opportunity_id: str):
    """
    Simulates: Screening -> Extraction -> Verification -> Scoring ->
    Market -> SWOT -> Memo, then writes every result back through
    crud.py. Sleeps briefly so the UI's spinner feels like a real
    call is happening -- remove the sleep once this is a real LLM call.

    Returns the updated Opportunity row.
    """
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    time.sleep(1.5)  # simulated "LLM thinking" delay

    has_github = bool(opportunity.github_url)

    # --- Screening ---------------------------------------------------
    # Real heuristics, keyed off field shape (length/presence), not off
    # any specific demo founder's name -- so this behaves the same way
    # once real sourced rows replace the mock ones.
    screen_status, screen_reason = _run_screening(opportunity)

    if screen_status == "failed":
        # Screened-out opportunities skip Extraction/Verification/
        # Scoring/Market/SWOT/Memo entirely -- write just the
        # screening result and stop.
        crud.update_opportunity(
            opportunity.id,
            screen_status=screen_status,
            screen_reason=screen_reason,
        )
        return crud.get_opportunity(opportunity.id)

    # --- Scoring (cold-start-aware) --------------------------------
    if has_github:
        founder_score = round(random.uniform(55, 85), 1)
        confidence_score = round(random.uniform(70, 90), 1)
        tier_note = "TrackRecord + ExecutionSignal both have evidence."
    else:
        founder_score = round(random.uniform(35, 65), 1)
        confidence_score = round(random.uniform(35, 55), 1)
        tier_note = "No GitHub/track record found -- weight redistributed into NarrativeQuality/ConsistencyScore."

    market_score = round(random.uniform(40, 85), 1)
    product_score = round(random.uniform(40, 85), 1)

    # --- Thesis fit (checked against live thesis_config) -----------
    thesis_config = crud.get_thesis_config()
    thesis_status = "in_thesis"
    thesis_reason = "Matches current thesis parameters."
    if thesis_config and thesis_config.sectors and opportunity.sector not in thesis_config.sectors:
        thesis_status = "outside_thesis"
        thesis_reason = f"Sector '{opportunity.sector}' not in current thesis sectors."

    # --- Evidence + claim -------------------------------------------
    evidence = crud.create_evidence_item(
        opportunity_id=opportunity.id,
        founder_id=opportunity.founder_id,
        source_type="llm_analysis",
        source_url="N/A",
        title="Simulated analysis pass",
        content=f"Simulated extraction + scoring run. {tier_note}",
        trust_score=round(random.uniform(0.5, 0.9), 2),
        reasoning=tier_note,
    )

    crud.create_claim(
        opportunity_id=opportunity.id,
        claim_type="traction",
        claim_value="Simulated claim extracted from available evidence.",
        confidence=round(random.uniform(0.5, 0.9), 2),
        reasoning="Generated by simulated pipeline, replace with real LLM extraction.",
        evidence_refs=[evidence.id],
    )

    # --- SWOT + Memo (derived, not a new "call") --------------------
    swot_summary = (
        f"Strengths: {'Verified GitHub activity.' if has_github else 'Coherent, specific pitch.'}\n"
        f"Weaknesses: {'Limited public track record.' if not has_github else 'Thin narrative detail.'}\n"
        f"Opportunities: Underserved segment in {opportunity.sector}.\n"
        f"Threats: {'None flagged.' if random.random() > 0.3 else 'Minor contradiction flagged in evidence.'}"
    )

    memo_md = f"""## {opportunity.company_name}

**Founder:** {opportunity.founder.name if opportunity.founder else 'Unknown'}
**Sector / Stage:** {opportunity.sector} / {opportunity.stage}
**Thesis fit:** {'Fits fund thesis' if thesis_status == 'in_thesis' else f'Outside thesis: {thesis_reason}'}

### Scores
- Founder: {founder_score} ({tier_note})
- Market: {market_score}
- Product: {product_score}
- Confidence: {confidence_score}

### Market size
[Not Disclosed] -- no reliable third-party estimate found in this simulated pass.
"""

    outreach_draft = (
        f"Hi {opportunity.founder.name if opportunity.founder else 'there'}, "
        f"came across {opportunity.company_name} and wanted to learn more about what you're building..."
        if opportunity.founder and opportunity.founder.source_type != "inbound"
        else None
    )

    # --- Write everything back --------------------------------------
    crud.update_opportunity(
        opportunity.id,
        screen_status=screen_status,
        screen_reason=screen_reason,
        thesis_status=thesis_status,
        thesis_reason=thesis_reason,
        swot_summary=swot_summary,
        memo_md=memo_md,
        outreach_draft=outreach_draft,
    )

    crud.record_score_history(
        opportunity_id=opportunity.id,
        founder_score=founder_score,
        market_score=market_score,
        product_score=product_score,
        confidence_score=confidence_score,
    )

    if opportunity.founder_id:
        crud.record_founder_score(
            founder_id=opportunity.founder_id,
            founder_score=founder_score,
            confidence_range=f"{max(0, founder_score-15):.0f}-{min(100, founder_score+15):.0f}",
            evidence_refs=[evidence.id],
            reasoning=tier_note,
        )

    return crud.get_opportunity(opportunity.id)


# ============================================================
# Mock detail generators -- stand-ins for dedicated per-tab LLM
# calls (Founder / Market / SWOT / Memo). Read-only: they don't
# write to the DB, just compute a fresh mock payload each render.
# Only meaningful for opportunities that have already been through
# run_analysis (screen_status != "pending").
# ============================================================

def get_founder_profile(opportunity_id: str) -> dict:
    """Mock founder deep-dive. Swap for a real LLM call later --
    keep the returned dict's keys stable since app.py renders them."""
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    founder = opportunity.founder
    has_github = bool(opportunity.github_url)

    return {
        "name": founder.name if founder else "Unknown",
        "background": (
            "Prior senior engineering role at a mid-size startup; shipped two "
            "open-source tools with meaningful adoption before founding this company."
            if has_github else
            "No public technical footprint found; background pieced together from "
            "the deck and LinkedIn -- prior operating role in a related industry, "
            "first-time technical founder."
        ),
        "education": random.choice([
            "State university, CS degree",
            "Bootcamp graduate, self-taught after",
            "No formal CS background -- domain expert turned builder",
        ]),
        "prior_companies": random.sample(
            [
                "a Y Combinator startup (acquired)",
                "a regional bank's fintech arm",
                "an early-stage healthtech startup",
                "a Series B devtools company",
                "no prior startup experience",
            ],
            k=1,
        ),
        "network_signal": (
            "Warm intros available through 2 existing portfolio founders."
            if random.random() > 0.5 else
            "No warm network overlap found yet."
        ),
        "risk_flags": [] if has_github else ["No verifiable public track record -- cold start."],
        "tier_note": opportunity.screen_reason or "Not yet screened.",
    }


def get_market_research(opportunity_id: str) -> dict:
    """Mock market sizing + competitive landscape. Swap for a real
    market-research LLM/tool call later."""
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    tam = random.choice([1.2, 3.5, 8.0, 15.0, 40.0])

    return {
        "sector": opportunity.sector,
        "tam_billion_usd": tam,
        "sam_billion_usd": round(tam * random.uniform(0.15, 0.4), 2),
        "growth_rate_pct": round(random.uniform(8, 45), 1),
        "competitors": random.sample(
            [
                "Ramp", "Brex", "Mercury", "Notion", "Linear", "Zapier",
                "a regional incumbent", "two other seed-stage startups in the same wedge",
            ],
            k=3,
        ),
        "differentiation": (
            f"Positions against incumbents on "
            f"{random.choice(['price', 'vertical focus', 'automation depth', 'onboarding speed'])}."
        ),
        "market_note": "[Simulated] No reliable third-party estimate found -- figures above are placeholder ranges.",
    }


def get_swot_analysis(opportunity_id: str) -> dict:
    """Parses the swot_summary text already written by run_analysis
    into a structured dict for the SWOT tab."""
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    parsed = {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []}
    if not opportunity.swot_summary:
        return parsed

    prefix_map = {
        "Strengths:": "strengths",
        "Weaknesses:": "weaknesses",
        "Opportunities:": "opportunities",
        "Threats:": "threats",
    }
    for line in opportunity.swot_summary.splitlines():
        for prefix, key in prefix_map.items():
            if line.startswith(prefix):
                parsed[key].append(line[len(prefix):].strip())
    return parsed


def get_memo(opportunity_id: str) -> dict:
    """Returns the memo + outreach draft already written by
    run_analysis, packaged for the Memo tab."""
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    return {
        "memo_md": opportunity.memo_md or "*Not yet analyzed.*",
        "outreach_draft": opportunity.outreach_draft,
    }


def get_evidence(opportunity_id: str) -> list[dict]:
    """Returns evidence_item rows for this opportunity, newest first,
    as plain dicts -- the raw signal Extraction/Verification read from,
    as opposed to get_claims()'s interpreted facts.

    Moved here from app.py so all data access for the UI goes through
    pipeline.py; app.py should only ever call into this module, never
    read crud/ORM rows itself.
    """
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    raw_evidence = None
    try:
        raw_evidence = list(opportunity.evidence_items)
    except Exception:
        raw_evidence = None

    if raw_evidence is None:
        for fn_name in (
            "get_evidence_for_opportunity",
            "get_evidence_items_by_opportunity",
            "get_evidence_by_opportunity",
            "get_evidence_items",
        ):
            fn = getattr(crud, fn_name, None)
            if fn:
                try:
                    raw_evidence = fn(opportunity_id)
                except Exception:
                    raw_evidence = None
                if raw_evidence is not None:
                    break

    if not raw_evidence:
        return []

    raw_evidence = sorted(raw_evidence, key=lambda e: getattr(e, "created_at", None) or 0, reverse=True)

    return [
        {
            "id": getattr(e, "id", None),
            "source_type": getattr(e, "source_type", "unknown"),
            "source_url": getattr(e, "source_url", "") or "",
            "title": getattr(e, "title", "") or "",
            "content": getattr(e, "content", "") or "",
            "trust_score": getattr(e, "trust_score", None),
            "reasoning": getattr(e, "reasoning", "") or "",
            "created_at": getattr(e, "created_at", None),
        }
        for e in raw_evidence
    ]


def get_claims(opportunity_id: str) -> list[dict]:
    """Returns the claim rows extracted for this opportunity, newest
    first, as plain dicts the UI can render directly -- claims are
    the *interpreted* facts Extraction pulled out of evidence, as
    opposed to get_evidence()'s raw signal.

    NOTE: tries opportunity.claims (SQLAlchemy relationship, same
    pattern as opportunity.founder) first, then falls back to a
    couple of likely crud function names. If your crud.py exposes
    a differently-named lookup, just point the first branch at it,
    e.g.: `raw_claims = crud.get_claims_for_opportunity(opportunity_id)`.
    """
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    raw_claims = None
    try:
        raw_claims = list(opportunity.claims)
    except Exception:
        raw_claims = None

    if raw_claims is None:
        for fn_name in ("get_claims_for_opportunity", "get_claims_by_opportunity", "get_claims"):
            fn = getattr(crud, fn_name, None)
            if fn:
                try:
                    raw_claims = fn(opportunity_id)
                except Exception:
                    raw_claims = None
                if raw_claims is not None:
                    break

    if not raw_claims:
        return []

    raw_claims = sorted(raw_claims, key=lambda c: getattr(c, "created_at", None) or 0, reverse=True)

    return [
        {
            "claim_type": getattr(c, "claim_type", "claim"),
            "claim_value": getattr(c, "claim_value", "") or "",
            "confidence": getattr(c, "confidence", None),
            "reasoning": getattr(c, "reasoning", "") or "",
            "evidence_refs": getattr(c, "evidence_refs", None) or [],
        }
        for c in raw_claims
    ]