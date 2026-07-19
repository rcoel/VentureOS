Expected LLm output format : 
{
  "extraction": {
    "claims": [
      {
        "claim_type": "traction",
        "claim_value": "string — the claim narrative",
        "confidence": 0.77,
        "reasoning": "string — short traceability note",
        "evidence_support": "supported | contradicted | unverifiable"
      }
    ]
  },
  "verification": {
    "contradictions": ["string", "..."],
    "plausibility_flags": ["string", "..."],
    "reasoning": "string"
  },
  "scoring": {
    "founder_score": 77.1,
    "market_score": 80.1,
    "product_score": 43.9,
    "confidence_score": 83.5,
    "reasoning": "string"
  },
  "activation": {
    "eligible": true,
    "outreach_draft": "string | null",
    "reasoning": "string"
  },
  "founder_profile": {
    "background": "string",
    "education": "string",
    "prior_companies": ["string", "..."],
    "network_signal": "string",
    "risk_flags": ["string", "..."]
  },
  "market_research": {
    "tam_billion_usd": 3.5,
    "sam_billion_usd": 0.73,
    "growth_rate_pct": 30.3,
    "competitors": ["string", "..."],
    "differentiation": "string",
    "market_note": "string",
    "bull_case": "string",
    "neutral_case": "string",
    "bear_case": "string"
  },
  "memo": {
    "executive_summary": "string",
    "founder_section": "string",
    "market_section": "string",
    "risks": ["string", "..."],
    "recommendation": "string"
  }
}