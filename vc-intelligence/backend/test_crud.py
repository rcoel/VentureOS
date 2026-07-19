import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.crud import (
    create_founder,
    get_founder,
    get_all_founders,
    update_founder,
    delete_founder,
    record_founder_score,
    get_founder_score_history,
    create_opportunity,
    get_opportunity,
    get_all_opportunities,
    get_opportunities_by_screen_status,
    get_opportunities_by_thesis_status,
    update_opportunity,
    delete_opportunity,
    create_evidence_item,
    get_evidence_for_opportunity,
    get_evidence_for_founder,
    create_claim,
    get_claims_for_opportunity,
    create_contradiction,
    get_contradictions_for_opportunity,
    record_score_history,
    get_score_history,
    get_score_trend,
    get_thesis_config,
    upsert_thesis_config,
)

print("=== Creating 2 founders ===")
founder_1 = create_founder(
    name="Jane Doe",
    bio="AI founder building B2B tools",
    links={"github": "https://github.com/jane", "linkedin": "https://linkedin.com/in/jane"},
    source_type="inbound",
    channel_instance="demo",
)
founder_2 = create_founder(
    name="John Smith",
    bio="Operator founder building vertical SaaS",
    links={"github": "https://github.com/john", "linkedin": "https://linkedin.com/in/john"},
    source_type="outbound",
    channel_instance="demo",
)
print(founder_1.id, founder_1.name)
print(founder_2.id, founder_2.name)
print("All founders:")
for founder in get_all_founders():
    print({
        "id": founder.id,
        "name": founder.name,
        "bio": founder.bio,
        "source_type": founder.source_type,
        "channel_instance": founder.channel_instance,
        "founder_score": founder.founder_score,
        "founder_score_confidence": founder.founder_score_confidence,
    })

print("\n=== Creating opportunities for both founders ===")
opp_1 = create_opportunity(
    founder_id=founder_1.id,
    company_name="Acme AI",
    description="Forecasting platform for SMBs",
    sector="AI",
    stage="lead",
    geography="US",
    screen_status="pending",
    thesis_status="draft",
)
opp_2 = create_opportunity(
    founder_id=founder_2.id,
    company_name="Northstar Labs",
    description="Workflow automation for agencies",
    sector="SaaS",
    stage="qualified",
    geography="EU",
    screen_status="passed",
    thesis_status="approved",
)
print(opp_1.id, opp_1.company_name)
print(opp_2.id, opp_2.company_name)
print("All opportunities:")
for opp in get_all_opportunities():
    print({
        "id": opp.id,
        "founder_id": opp.founder_id,
        "company_name": opp.company_name,
        "description": opp.description,
        "sector": opp.sector,
        "stage": opp.stage,
        "geography": opp.geography,
        "screen_status": opp.screen_status,
        "thesis_status": opp.thesis_status,
        "founder_score": opp.founder_score,
        "market_score": opp.market_score,
        "product_score": opp.product_score,
        "confidence_score": opp.confidence_score,
    })

print("\n=== Adding related data to each opportunity ===")
for opp, founder in [(opp_1, founder_1), (opp_2, founder_2)]:
    create_evidence_item(
        opportunity_id=opp.id,
        founder_id=founder.id,
        source_type="github",
        source_url="https://example.com",
        title=f"Evidence for {opp.company_name}",
        content="Good signal from repo activity",
        trust_score=0.9,
        reasoning="Strong signal",
        evidence_refs=["repo:123"],
    )
    create_claim(
        opportunity_id=opp.id,
        claim_type="thesis",
        claim_value=f"Strong thesis for {opp.company_name}",
        confidence=0.85,
        reasoning="Good fit for the market",
        evidence_refs=["repo:123"],
    )
    create_contradiction(
        opportunity_id=opp.id,
        description=f"Needs more proof for {opp.company_name}",
        severity="medium",
        reasoning="More evidence needed",
        evidence_refs=["repo:456"],
    )
    record_score_history(
        opportunity_id=opp.id,
        founder_score=8.0,
        market_score=7.5,
        product_score=7.0,
        confidence_score=0.8,
    )
    record_founder_score(
        founder_id=founder.id,
        founder_score=8.0,
        confidence_range="75-85",
        evidence_refs=["evidence-1"],
        reasoning="Solid founder signal",
    )

print("\n=== Displaying data from each table ===")
print("Founders:")
print(get_all_founders())
print("\nOpportunities:")
print(get_all_opportunities())
print("\nEvidence for founder 1:")
for item in get_evidence_for_founder(founder_1.id):
    print({
        "id": item.id,
        "title": item.title,
        "source_type": item.source_type,
        "source_url": item.source_url,
        "content": item.content,
        "trust_score": item.trust_score,
    })
print("\nEvidence for founder 2:")
for item in get_evidence_for_founder(founder_2.id):
    print({
        "id": item.id,
        "title": item.title,
        "source_type": item.source_type,
        "source_url": item.source_url,
        "content": item.content,
        "trust_score": item.trust_score,
    })
print("\nClaims for opp 1:")
for claim in get_claims_for_opportunity(opp_1.id):
    print({
        "id": claim.id,
        "claim_type": claim.claim_type,
        "claim_value": claim.claim_value,
        "confidence": claim.confidence,
        "reasoning": claim.reasoning,
    })
print("\nContradictions for opp 2:")
for contradiction in get_contradictions_for_opportunity(opp_2.id):
    print({
        "id": contradiction.id,
        "description": contradiction.description,
        "severity": contradiction.severity,
        "reasoning": contradiction.reasoning,
    })
print("\nScore history for opp 1:")
for history in get_score_history(opp_1.id):
    print({
        "id": history.id,
        "founder_score": history.founder_score,
        "market_score": history.market_score,
        "product_score": history.product_score,
        "confidence_score": history.confidence_score,
    })
print("\nFounder score history for founder 2:")
for history in get_founder_score_history(founder_2.id):
    print({
        "id": history.id,
        "founder_score": history.founder_score,
        "confidence_range": history.confidence_range,
        "reasoning": history.reasoning,
    })

print("\n=== Thesis config ===")
config = upsert_thesis_config(
    sectors=["SaaS", "AI"],
    stage="lead",
    geography="US",
    check_size_min=100000,
    check_size_max=5000000,
    ownership_target=0.2,
    risk_appetite="medium",
)
print({
    "id": config.id,
    "sectors": config.sectors,
    "stage": config.stage,
    "geography": config.geography,
    "check_size_min": config.check_size_min,
    "check_size_max": config.check_size_max,
    "ownership_target": config.ownership_target,
    "risk_appetite": config.risk_appetite,
})
print("Current thesis config:")
print({
    "id": get_thesis_config().id,
    "sectors": get_thesis_config().sectors,
    "stage": get_thesis_config().stage,
    "geography": get_thesis_config().geography,
    "check_size_min": get_thesis_config().check_size_min,
    "check_size_max": get_thesis_config().check_size_max,
    "ownership_target": get_thesis_config().ownership_target,
    "risk_appetite": get_thesis_config().risk_appetite,
})

print("\n=== Deleting one founder ===")
delete_founder(founder_1.id)
print("Remaining founders:")
for founder in get_all_founders():
    print({
        "id": founder.id,
        "name": founder.name,
        "bio": founder.bio,
    })
print("Remaining opportunities:")
for opp in get_all_opportunities():
    print({
        "id": opp.id,
        "company_name": opp.company_name,
        "founder_id": opp.founder_id,
    })
