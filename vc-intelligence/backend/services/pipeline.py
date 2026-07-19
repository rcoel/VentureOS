"""
pipeline.py -- simulated version of Person A's real LangGraph pipeline.

=====================================================================
ARCHITECTURE (Phase 3-5 consolidation)
=====================================================================
Two entry points the dashboard calls directly:

  seed_initial_data()
      Simulates the outbound sourcing scan (GitHub/HN/Devpost/etc).
      Creates Founder + Opportunity rows with only the "raw intake"
      fields filled in -- no scores yet, screen_status="pending".

  run_analysis(opportunity_id)
      Runs Screening (real rule-based logic, not an LLM call), then
      makes ONE mock LLM call -- _mock_llm_analysis_call() -- that
      returns a single JSON object covering Extraction, Verification,
      Scoring, Activation, Founder profile, and Market research in
      one shot. SWOT is deliberately NOT part of that JSON -- it's a
      derived view computed here from the other pieces, per spec
      ("no extra LLM call"). Every piece of that JSON is written to
      the DB exactly once per Analyze/Re-analyze click:
        - claims + evidence rows (Extraction)
        - opportunity.founder_score / market_score / product_score /
          confidence_score + score_history (Scoring)
        - opportunity.outreach_draft (Activation)
        - opportunity.swot_summary (derived SWOT)
        - opportunity.memo_md (assembled Memo, 5 sections + inline
          trust scores + thesis line, per spec)
        - opportunity.analysis_json -- the FULL raw JSON, verbatim,
          so every read-only tab below can render straight from
          storage instead of recomputing/re-randomizing on every
          Streamlit rerun.

      NOTE -- schema dependency: this requires one new column on the
      Opportunity model: `analysis_json` (Text, nullable). It isn't
      in the files I was given (models.py/crud.py weren't uploaded),
      so add that column there; crud.update_opportunity(...) should
      already persist it for free the same way it does every other
      kwarg passed to it elsewhere in this file. Until that column
      exists, run_analysis() will raise on the update_opportunity()
      call below -- everything else in this module is independent of
      that column and will keep working.

Four read-only detail generators (Founder / Market / SWOT / Memo) now
do a PURE DB READ -- they parse opportunity.analysis_json (or, for
SWOT/Memo, the already-formatted text fields) and return it as-is.
They no longer fabricate fresh random values on every render -- the
single mock LLM call above is the only place randomness is introduced,
and it only runs when Analyze/Re-analyze is clicked. Swap the body of
_mock_llm_analysis_call for a real LLM/LangGraph call later; every
return dict shape below is unchanged, so app.py won't need to change.
"""

import json
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
# Screening -- real rule-based logic, not an LLM call. Runs
# before the LLM call below so screened-out opportunities never
# waste a call on Extraction/Scoring/Market/SWOT/Memo.
# ============================================================

MIN_DESCRIPTION_LENGTH = 15  # chars -- "TBD" etc. won't clear this


def _run_screening(opportunity) -> tuple[str, str]:
    """Checks field shape (length/presence), not any specific demo
    founder's name, so it behaves the same way once real sourced rows
    arrive. Returns (screen_status, screen_reason)."""
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


# ============================================================
# Single consolidated LLM call -- SWAP POINT
# ============================================================

# Founder score threshold above which an outbound founder gets a
# drafted cold-outreach message (Activation). Inbound founders never
# get one, regardless of score, since they already reached out to us.
ACTIVATION_SCORE_THRESHOLD = 60.0


# The exact JSON shape we're assuming the real LLM call will return,
# in one shot, per Analyze/Re-analyze click. Every key here must be
# present with these types once the mock body below is swapped for a
# real call -- run_analysis() only ever reads this dict, so this
# contract is what keeps app.py and run_analysis() from needing to
# change when the swap happens.
#
# SWOT is intentionally absent -- it's derived in run_analysis() from
# founder_profile / market_research / verification below, no extra
# call needed.
FULL_ANALYSIS_OUTPUT_SHAPE = {
    "extraction": {
        # list[dict], each: {claim_type: str, claim_value: str,
        # confidence: float(0-1), reasoning: str,
        # evidence_support: "supported" | "contradicted" | "unverifiable"}
        "claims": list,
    },
    "verification": {
        "contradictions": list,       # list[str] -- cross-source contradictions found, [] if none
        "plausibility_flags": list,   # list[str] -- funding/traction/hiring vs comparable-norm flags, [] if none
        "reasoning": str,
    },
    "scoring": {
        "founder_score": float,       # 0-100
        "market_score": float,        # 0-100
        "product_score": float,       # 0-100
        "confidence_score": float,    # 0-100, how sure the model is in these scores
        "reasoning": str,             # short human-readable note on how scores were derived
    },
    "activation": {
        "eligible": bool,             # outbound source + founder_score >= ACTIVATION_SCORE_THRESHOLD
        "outreach_draft": str,        # or None if not eligible
        "reasoning": str,
    },
    "founder_profile": {
        "background": str,
        "education": str,
        "prior_companies": list,      # list[str]
        "network_signal": str,
        "risk_flags": list,           # list[str], [] if none
    },
    "market_research": {
        "tam_billion_usd": float,     # or None if sector unknown -- explicit "not found" fallback
        "sam_billion_usd": float,     # or None
        "growth_rate_pct": float,     # or None
        "competitors": list,          # list[str]
        "differentiation": str,
        "market_note": str,           # explicit note when no reliable estimate found
        "bull_case": str,
        "neutral_case": str,
        "bear_case": str,
    },
    "memo": {
        # Raw prose/content only -- run_analysis() does the actual
        # Markdown assembly (headers, inline trust scores, thesis
        # line, [Not Disclosed] gap-filling) around these pieces.
        "executive_summary": str,
        "founder_section": str,
        "market_section": str,
        "risks": list,                # list[str]
        "recommendation": str,
    },
}


def _mock_llm_analysis_call(opportunity, evidence_items: list[dict]) -> dict:
    """SWAP POINT -- stand-in for the real LLM/LangGraph call.

    ---------------------------------------------------------------
    INPUT the real call will be given (single JSON payload):
    ---------------------------------------------------------------
    {
        "opportunity": {
            "company_name": str, "description": str,
            "sector": str | None, "stage": str | None,
            "geography": str | None, "github_url": str | None,
        },
        "founder": {
            "name": str, "bio": str,
            "source_type": str, "links": dict,
        },
        "evidence": [
            {"source_type": str, "source_url": str, "title": str,
             "content": str, "trust_score": float, "reasoning": str},
            ...
        ],
        "thesis_config": {
            "sectors": list[str], "stage": str,
            "geography": str, "risk_appetite": str,
        } | None,
    }

    ---------------------------------------------------------------
    OUTPUT the real call must return: FULL_ANALYSIS_OUTPUT_SHAPE
    ---------------------------------------------------------------
    See the dict above. One JSON object, one call, per Analyze click.

    Mock version below fabricates a plausible payload in that exact
    shape -- cold-start-aware based on github_url/evidence presence,
    same as the old per-phase mocks -- so run_analysis() can be
    written against the final contract now. Swap only this function's
    body for the real call later; nothing else changes.
    """
    has_github = bool(opportunity.github_url)
    is_cold_start = not has_github and not evidence_items
    founder = opportunity.founder
    founder_name = founder.name if founder else "Unknown"
    source_type = founder.source_type if founder else None

    # ---- Extraction ------------------------------------------------
    claims = [
        {
            "claim_type": "traction",
            "claim_value": (
                f"{opportunity.company_name} shows credible early traction with "
                f"public activity to verify it."
                if has_github else
                f"{opportunity.company_name} presents an early-stage narrative with "
                f"no independently verifiable activity yet."
            ),
            "confidence": round(random.uniform(0.55, 0.9), 2) if has_github else round(random.uniform(0.3, 0.55), 2),
            "reasoning": (
                "Derived from GitHub activity signal."
                if has_github else
                "No public repo found in evidence -- narrative-only claim."
            ),
            "evidence_support": "supported" if has_github else "unverifiable",
        },
        {
            "claim_type": "funding",
            "claim_value": f"No external funding round disclosed for {opportunity.company_name}.",
            "confidence": round(random.uniform(0.4, 0.7), 2),
            "reasoning": "No funding-announcement evidence found in sourced signals.",
            "evidence_support": "unverifiable",
        },
        {
            "claim_type": "hiring",
            "claim_value": f"{founder_name} appears to be operating solo at this stage.",
            "confidence": round(random.uniform(0.4, 0.75), 2),
            "reasoning": "No hiring signal (job posts, team page) found in evidence.",
            "evidence_support": "unverifiable",
        },
    ]

    # ---- Verification: contradiction check + plausibility pass -----
    plausibility_flags = []
    if not has_github and source_type != "inbound":
        plausibility_flags.append(
            "Traction claim not corroborated by any public artifact -- implausible vs. comparable outbound founders."
        )
    contradictions = []
    if random.random() < 0.2:
        contradictions.append(
            "Stage listed as Pre-seed but description implies later-stage traction -- flagged, not resolved."
        )
    verification = {
        "contradictions": contradictions,
        "plausibility_flags": plausibility_flags,
        "reasoning": "Cross-checked claim narrative against evidence source_type, trust scores, and stage-comparable norms.",
    }

    # ---- Scoring (cold-start reweighting) ---------------------------
    if has_github:
        founder_score = round(random.uniform(55, 85), 1)
        confidence_score = round(random.uniform(70, 90), 1)
        scoring_reasoning = "TrackRecord + ExecutionSignal both have evidence."
    elif is_cold_start:
        founder_score = round(random.uniform(30, 55), 1)
        confidence_score = round(random.uniform(25, 45), 1)
        scoring_reasoning = "Cold start: no GitHub, no prior evidence on file -- weight fully redistributed into NarrativeQuality."
    else:
        founder_score = round(random.uniform(35, 65), 1)
        confidence_score = round(random.uniform(35, 55), 1)
        scoring_reasoning = "No GitHub/track record found -- weight redistributed into NarrativeQuality/ConsistencyScore."

    scoring = {
        "founder_score": founder_score,
        "market_score": round(random.uniform(40, 85), 1),
        "product_score": round(random.uniform(40, 85), 1),
        "confidence_score": confidence_score,
        "reasoning": scoring_reasoning,
    }

    # ---- Activation --------------------------------------------------
    eligible = bool(source_type) and source_type != "inbound" and founder_score >= ACTIVATION_SCORE_THRESHOLD
    activation = {
        "eligible": eligible,
        "outreach_draft": (
            f"Hi {founder_name}, came across {opportunity.company_name} and wanted "
            f"to learn more about what you're building..."
            if eligible else None
        ),
        "reasoning": (
            f"Outbound source ('{source_type}') + founder_score {founder_score} >= "
            f"{ACTIVATION_SCORE_THRESHOLD} threshold."
            if eligible else
            "Not eligible -- inbound source or founder_score below activation threshold."
        ),
    }

    # ---- Founder profile ---------------------------------------------
    risk_flags = [] if has_github else ["No verifiable public track record -- cold start."]
    if is_cold_start:
        risk_flags.append("Deck-only intake -- no independent source corroborates founder claims yet.")

    founder_profile = {
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
        "risk_flags": risk_flags,
    }

    # ---- Market research (explicit "not found" fallback) -------------
    if opportunity.sector:
        tam = random.choice([1.2, 3.5, 8.0, 15.0, 40.0])
        market_research = {
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
            "bull_case": f"{opportunity.sector} is expanding and {opportunity.company_name} has a defensible early wedge.",
            "neutral_case": "Market is real but crowded; differentiation will decide the outcome.",
            "bear_case": "Incumbents could bundle this feature and compress the wedge.",
        }
    else:
        market_research = {
            "tam_billion_usd": None,
            "sam_billion_usd": None,
            "growth_rate_pct": None,
            "competitors": [],
            "differentiation": "[Not Disclosed]",
            "market_note": "[Not Disclosed] -- no sector provided, market sizing search skipped.",
            "bull_case": "[Not Disclosed]",
            "neutral_case": "[Not Disclosed]",
            "bear_case": "[Not Disclosed]",
        }

    # ---- Memo copy (raw content -- Markdown assembly happens in run_analysis) --
    memo = {
        "executive_summary": (
            f"{opportunity.company_name} is a {opportunity.stage or '[Not Disclosed]'}-stage "
            f"{opportunity.sector or '[Not Disclosed]'} company. {claims[0]['claim_value']}"
        ),
        "founder_section": founder_profile["background"],
        "market_section": market_research["bull_case"] if opportunity.sector else "[Not Disclosed]",
        "risks": (plausibility_flags + contradictions) or ["No material risks flagged in this pass."],
        "recommendation": (
            "Proceed to founder call." if founder_score >= 60 else
            "Pass for now -- revisit if traction signal improves."
        ),
    }

    return {
        "extraction": {"claims": claims},
        "verification": verification,
        "scoring": scoring,
        "activation": activation,
        "founder_profile": founder_profile,
        "market_research": market_research,
        "memo": memo,
    }


def run_analysis(opportunity_id: str):
    """
    Screening (real, rule-based) -> one mock LLM call covering
    Extraction/Verification/Scoring/Activation/Founder/Market ->
    derived SWOT -> assembled Memo. Writes every result back through
    crud.py, once, per click. Sleeps briefly so the UI's spinner
    feels like a real call is happening -- remove the sleep once this
    is a real LLM call.

    Returns the updated Opportunity row.
    """
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    time.sleep(1.5)  # simulated "LLM thinking" delay

    # --- Screening ---------------------------------------------------
    screen_status, screen_reason = _run_screening(opportunity)

    if screen_status == "failed":
        # Screened-out opportunities skip Extraction/Scoring/Market/
        # SWOT/Memo entirely -- write just the screening result and stop.
        crud.update_opportunity(
            opportunity.id,
            screen_status=screen_status,
            screen_reason=screen_reason,
        )
        return crud.get_opportunity(opportunity.id)

    # --- Thesis fit (checked against live thesis_config) -------------
    thesis_config = crud.get_thesis_config()
    thesis_status = "in_thesis"
    thesis_reason = "Matches current thesis parameters."
    if thesis_config and thesis_config.sectors and opportunity.sector not in thesis_config.sectors:
        thesis_status = "outside_thesis"
        thesis_reason = f"Sector '{opportunity.sector}' not in current thesis sectors."

    # --- The single LLM call ------------------------------------------
    existing_evidence = get_evidence(opportunity.id)
    full = _mock_llm_analysis_call(opportunity, existing_evidence)

    # --- Persist Extraction: one evidence + claim row per claim -------
    created_claims = []
    for claim in full["extraction"]["claims"]:
        evidence = crud.create_evidence_item(
            opportunity_id=opportunity.id,
            founder_id=opportunity.founder_id,
            source_type="llm_analysis",
            source_url="N/A",
            title=f"Analysis pass -- {claim['claim_type']}",
            content=claim["claim_value"],
            trust_score=claim["confidence"],
            reasoning=claim["reasoning"],  # Agentic Traceability: reasoning logged on every write
        )
        crud.create_claim(
            opportunity_id=opportunity.id,
            claim_type=claim["claim_type"],
            claim_value=claim["claim_value"],
            confidence=claim["confidence"],
            reasoning=claim["reasoning"],
            evidence_refs=[evidence.id],
        )
        created_claims.append({**claim, "evidence_id": evidence.id, "trust_score": claim["confidence"]})

    # --- Scoring --------------------------------------------------------
    scoring = full["scoring"]
    founder_score = scoring["founder_score"]
    market_score = scoring["market_score"]
    product_score = scoring["product_score"]
    confidence_score = scoring["confidence_score"]

    # --- Activation -------------------------------------------------
    outreach_draft = full["activation"]["outreach_draft"]

    # --- SWOT: derived view, no extra LLM call -----------------------
    founder_profile = full["founder_profile"]
    market_research = full["market_research"]
    verification = full["verification"]
    has_github = bool(opportunity.github_url)

    strengths = []
    if has_github:
        strengths.append("Verified public track record (GitHub activity).")
    if not founder_profile["risk_flags"]:
        strengths.append("No founder-side risk flags raised.")
    if not strengths:
        strengths.append("Coherent, specific pitch despite limited external evidence.")

    weaknesses = list(founder_profile["risk_flags"]) or ["No material weaknesses flagged in this pass."]

    opportunities_list = (
        [f"Underserved segment in {opportunity.sector}: {market_research['differentiation']}"]
        if opportunity.sector else
        ["[Not Disclosed] -- no sector to derive a market gap from."]
    )

    threats = list(verification["contradictions"]) + list(verification["plausibility_flags"])
    if opportunity.sector and market_research.get("bear_case"):
        threats.append(market_research["bear_case"])
    if not threats:
        threats = ["None flagged."]

    swot_summary = (
        f"Strengths: {'; '.join(strengths)}\n"
        f"Weaknesses: {'; '.join(weaknesses)}\n"
        f"Opportunities: {'; '.join(opportunities_list)}\n"
        f"Threats: {'; '.join(threats)}"
    )

    # --- Memo assembly: 5 sections, inline trust score per claim, ---
    # explicit [Not Disclosed] gaps, explicit thesis line -----------
    thesis_line = "Fits Thesis" if thesis_status == "in_thesis" else f"Outside Thesis: {thesis_reason}"

    claim_lines = []
    for c in created_claims:
        trust = c.get("trust_score")
        trust_label = f"(Trust Score: {trust * 100:.0f}%)" if trust is not None else "(Trust Score: [Not Disclosed])"
        claim_lines.append(f"- **{c['claim_type'].title()}**: {c['claim_value']} {trust_label}")
    claims_block = "\n".join(claim_lines) if claim_lines else "- [Not Disclosed] -- no claims extracted this pass."

    risks_block = "\n".join(f"- {r}" for r in full["memo"]["risks"])

    memo_md = f"""## {opportunity.company_name}

**Founder:** {opportunity.founder.name if opportunity.founder else '[Not Disclosed]'}
**Sector / Stage:** {opportunity.sector or '[Not Disclosed]'} / {opportunity.stage or '[Not Disclosed]'}
**Thesis fit:** {thesis_line}

### Executive Summary
{full['memo']['executive_summary']}

### Founder
{full['memo']['founder_section']}

### Market
{full['memo']['market_section']}

### Claims (inline trust score)
{claims_block}

### Risks
{risks_block}

### Recommendation
{full['memo']['recommendation']}

### Scores
- Founder: {founder_score} ({scoring['reasoning']})
- Market: {market_score}
- Product: {product_score}
- Confidence: {confidence_score}
"""

    # --- Write everything back, once ---------------------------------
    # analysis_json is the full raw payload -- every read-only get_*
    # function below parses this back out instead of recomputing
    # anything, so re-rendering a tab never re-randomizes the data;
    # only a fresh Analyze/Re-analyze click ever changes it.
    crud.update_opportunity(
        opportunity.id,
        screen_status=screen_status,
        screen_reason=screen_reason,
        thesis_status=thesis_status,
        thesis_reason=thesis_reason,
        swot_summary=swot_summary,
        memo_md=memo_md,
        outreach_draft=outreach_draft,
        analysis_json=json.dumps(full),
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
            confidence_range=f"{max(0, founder_score - 15):.0f}-{min(100, founder_score + 15):.0f}",
            evidence_refs=[c["evidence_id"] for c in created_claims],
            reasoning=scoring["reasoning"],
        )

    return crud.get_opportunity(opportunity.id)


# ============================================================
# Read-only detail generators -- Founder / Market / SWOT / Memo.
# Pure DB reads now: no randomness, no recomputation on render.
# Only meaningful for opportunities that have already been through
# run_analysis (screen_status != "pending"); before that they return
# a "not yet analyzed" shaped default so app.py can render safely.
# ============================================================

def _load_analysis_json(opportunity) -> dict:
    """Parses opportunity.analysis_json (written once per Analyze
    click by run_analysis) back into the FULL_ANALYSIS_OUTPUT_SHAPE
    dict. Returns {} if not yet analyzed or the column is empty --
    callers below fall back to '[Not Disclosed]'-style defaults."""
    raw = getattr(opportunity, "analysis_json", None)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def get_founder_profile(opportunity_id: str) -> dict:
    """Founder deep-dive, read straight from the stored analysis_json
    -- keep the returned dict's keys stable since app.py renders them."""
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    founder = opportunity.founder
    profile = _load_analysis_json(opportunity).get("founder_profile", {})

    return {
        "name": founder.name if founder else "Unknown",
        "background": profile.get("background") or "Not yet analyzed -- click Analyze first.",
        "education": profile.get("education") or "[Not Disclosed]",
        "prior_companies": profile.get("prior_companies") or [],
        "network_signal": profile.get("network_signal") or "[Not Disclosed]",
        "risk_flags": profile.get("risk_flags") or [],
        "tier_note": opportunity.screen_reason or "Not yet screened.",
    }


def get_market_research(opportunity_id: str) -> dict:
    """Market sizing + competitive landscape, read straight from the
    stored analysis_json."""
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    research = _load_analysis_json(opportunity).get("market_research", {})

    return {
        "sector": opportunity.sector,
        # Numeric fields fall back to 0.0 (not None) so app.py's
        # f"${...}B" metric formatting never breaks; market_note
        # carries the explicit "[Not Disclosed]" signal instead.
        "tam_billion_usd": research.get("tam_billion_usd") if research.get("tam_billion_usd") is not None else 0.0,
        "sam_billion_usd": research.get("sam_billion_usd") if research.get("sam_billion_usd") is not None else 0.0,
        "growth_rate_pct": research.get("growth_rate_pct") if research.get("growth_rate_pct") is not None else 0.0,
        "competitors": research.get("competitors") or [],
        "differentiation": research.get("differentiation") or "[Not Disclosed]",
        "market_note": research.get("market_note") or "[Not Disclosed] -- not yet analyzed.",
    }


def get_swot_analysis(opportunity_id: str) -> dict:
    """Parses the swot_summary text already written by run_analysis
    into a structured dict for the SWOT tab. Unchanged pattern --
    swot_summary is DB-stored text, not recomputed here."""
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
    run_analysis, packaged for the Memo tab. DB-only, as before."""
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


# ============================================================
# Score history / trend -- read-only, drives Phase 4's Founder page
# trend + history chart and the Overview list's trend arrows. Reads
# rows written by run_analysis()'s crud.record_score_history() /
# crud.record_founder_score() calls each time an opportunity is
# (re-)analyzed -- nothing new to mock here, just surfacing what's
# already being written.
# ============================================================

def get_score_history(opportunity_id: str) -> list[dict]:
    """This opportunity's score_history rows, oldest first -- one row
    per completed run_analysis() call. Empty list until it's been
    analyzed at least once; a single-entry list until re-analyzed."""
    opportunity = crud.get_opportunity(opportunity_id)
    if not opportunity:
        raise ValueError(f"No opportunity found for id={opportunity_id}")

    raw_history = None
    try:
        raw_history = list(opportunity.score_history)
    except Exception:
        raw_history = None

    if raw_history is None:
        for fn_name in (
            "get_score_history_for_opportunity",
            "get_score_history_by_opportunity",
            "get_opportunity_score_history",
        ):
            fn = getattr(crud, fn_name, None)
            if fn:
                try:
                    raw_history = fn(opportunity_id)
                except Exception:
                    raw_history = None
                if raw_history is not None:
                    break

    if not raw_history:
        return []

    raw_history = sorted(raw_history, key=lambda h: getattr(h, "created_at", None) or 0)

    return [
        {
            "founder_score": getattr(h, "founder_score", None),
            "market_score": getattr(h, "market_score", None),
            "product_score": getattr(h, "product_score", None),
            "confidence_score": getattr(h, "confidence_score", None),
            "created_at": getattr(h, "created_at", None),
        }
        for h in raw_history
    ]


def get_score_trend(opportunity_id: str) -> dict:
    """Composite trend (avg of founder/market/product) between the
    last two score_history entries -- drives the Overview list's
    trend arrow. direction is "new" when there's zero or one entry
    (nothing to compare against yet)."""
    history = get_score_history(opportunity_id)
    if not history:
        return {"direction": "new", "delta": 0.0, "current": None, "history": []}

    def composite(entry):
        vals = [v for v in (entry["founder_score"], entry["market_score"], entry["product_score"]) if v is not None]
        return sum(vals) / len(vals) if vals else None

    composites = [composite(h) for h in history]
    current = composites[-1]

    if len(composites) < 2 or composites[-2] is None or current is None:
        return {"direction": "new", "delta": 0.0, "current": current, "history": history}

    delta = round(current - composites[-2], 1)
    if delta > 0.5:
        direction = "up"
    elif delta < -0.5:
        direction = "down"
    else:
        direction = "flat"

    return {"direction": direction, "delta": delta, "current": current, "history": history}


def get_founder_score_history(founder_id: str) -> list[dict]:
    """This founder's founder_score history rows, oldest first -- one
    row per completed run_analysis() call that touched this founder.
    Drives the Founder page's confidence range + history chart."""
    founder = crud.get_founder(founder_id)
    if not founder:
        raise ValueError(f"No founder found for id={founder_id}")

    raw_history = None
    try:
        raw_history = list(founder.score_history)
    except Exception:
        raw_history = None

    if raw_history is None:
        for fn_name in (
            "get_founder_score_history",
            "get_scores_for_founder",
            "get_founder_scores",
        ):
            fn = getattr(crud, fn_name, None)
            if fn:
                try:
                    raw_history = fn(founder_id)
                except Exception:
                    raw_history = None
                if raw_history is not None:
                    break

    if not raw_history:
        return []

    raw_history = sorted(raw_history, key=lambda h: getattr(h, "created_at", None) or 0)

    return [
        {
            "founder_score": getattr(h, "founder_score", None),
            "confidence_range": getattr(h, "confidence_range", None),
            "reasoning": getattr(h, "reasoning", None),
            "created_at": getattr(h, "created_at", None),
        }
        for h in raw_history
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