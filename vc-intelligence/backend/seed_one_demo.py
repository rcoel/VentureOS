import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.crud import (
    create_founder,
    record_founder_score,
    create_opportunity,
    create_evidence_item,
    create_claim,
    create_contradiction,
    record_score_history,
    upsert_thesis_config,
)

founder = create_founder(
    name="Ava Patel",
    bio="Founder building AI copilots for legal teams.",
    links={"github": "https://github.com/ava", "linkedin": "https://linkedin.com/in/ava"},
    source_type="inbound",
    channel_instance="demo",
)

record_founder_score(
    founder_id=founder.id,
    founder_score=8.4,
    confidence_range="80-90",
    evidence_refs=["repo:123"],
    reasoning="Strong founder signal",
)

opportunity = create_opportunity(
    founder_id=founder.id,
    company_name="LexPilot AI",
    description="AI copilots for contract review and legal drafting.",
    sector="AI Applications",
    stage="Seed",
    geography="US",
    screen_status="passed",
    thesis_status="approved",
)

create_evidence_item(
    opportunity_id=opportunity.id,
    founder_id=founder.id,
    source_type="github",
    source_url="https://github.com/lexpilot",
    title="Strong repo activity",
    content="Active product development and large weekly commit volume.",
    trust_score=0.91,
    reasoning="Good signal",
    evidence_refs=["repo:123"],
)

create_claim(
    opportunity_id=opportunity.id,
    claim_type="thesis",
    claim_value="The product is clearly differentiated in legal workflows.",
    confidence=0.88,
    reasoning="Strong market fit evidence",
    evidence_refs=["repo:123"],
)

create_contradiction(
    opportunity_id=opportunity.id,
    description="Customer traction is still early and not yet public.",
    severity="medium",
    reasoning="More evidence needed on revenue traction",
    evidence_refs=["repo:456"],
)

record_score_history(
    opportunity_id=opportunity.id,
    founder_score=8.4,
    market_score=7.8,
    product_score=8.1,
    confidence_score=0.86,
)

upsert_thesis_config(
    sectors=["AI Applications", "Enterprise SaaS"],
    stage="Seed",
    geography="US",
    check_size_min=250000,
    check_size_max=2000000,
    ownership_target=15.0,
    risk_appetite="Medium",
)

print("Seeded one founder and related records successfully.")
print("Founder:", founder.name)
print("Opportunity:", opportunity.company_name)
