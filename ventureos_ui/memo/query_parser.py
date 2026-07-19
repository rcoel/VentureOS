"""Natural-language query → typed QueryFilter → SQL WHERE clause.

Uses the QueryFilter Pydantic model already defined in ventureos.models.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ventureos.models import QueryFilter
from ventureos_ui.models_orm import Founder, FounderScore

# The query-parser prompt is inline to avoid a new prompt file. Keep the
# instructions tight and schema-focused.
_SYSTEM_PROMPT = """
You translate a plain-English investor query into a structured QueryFilter.

Return only fields the query actually mentions or clearly implies. Leave the
rest as null (or empty list). Do NOT fabricate constraints.

Field guide:
- is_technical: true if the query says "technical founder", "engineer",
  "CTO", "shipped a repo". false if it says "non-technical" or "business
  founder". null otherwise.
- location_contains: extract a substring — e.g. "Berlin", "SF", "Europe".
- categories_any: list of category strings mentioned — "AI infra",
  "dev tools", etc. Normalize casing but keep the phrasing.
- customer_segment: only "consumer" | "smb" | "enterprise" | "developer".
- prior_vc_backing: true / false / null (only if the query is explicit).
- accelerator_tier: "yc" | "techstars" | "other" | "none" | null.
- min_prior_exits, is_researcher, min_h_index: fill only when the query
  explicitly says so.
"""


async def _parse_with_llm(nl_query: str) -> QueryFilter:
    """Call OpenAI to parse the query. Falls back to empty filter on error."""
    try:
        from ventureos.llm import fast_model, openai_json

        return await openai_json(
            system=_SYSTEM_PROMPT,
            user={"query": nl_query},
            schema=QueryFilter,
            model=fast_model(),
        )
    except Exception:
        return QueryFilter()


def parse_query(nl_query: str) -> QueryFilter:
    """Sync wrapper for Streamlit call sites."""
    return asyncio.run(_parse_with_llm(nl_query))


# --------------------------------------------------------------------------- #
# Filter → SQL                                                                #
# --------------------------------------------------------------------------- #


def _matches_json_field(founder: Founder, key: str, expected: Any) -> bool:
    """Post-query Python filter for JSON columns.

    SQLite's JSON1 doesn't have clean SQLAlchemy expressions for equality on
    arbitrary JSON keys, so we filter in Python. This is fine for demo scale
    (dozens of founders).
    """
    attrs = founder.attributes or {}
    if not isinstance(attrs, dict):
        return False
    val = attrs.get(key)
    return val == expected


def apply_filter(session: Session, qf: QueryFilter) -> list[Founder]:
    """Apply the QueryFilter to the DB and return matching founders,
    ranked by FounderScore desc."""
    stmt = (
        select(Founder, FounderScore.founder_score)
        .join(FounderScore, FounderScore.founder_id == Founder.id, isouter=True)
    )

    results = list(session.execute(stmt).all())

    filtered: list[tuple[Founder, float | None]] = []
    for founder, score in results:
        attrs = founder.attributes if isinstance(founder.attributes, dict) else {}

        # is_technical
        if qf.is_technical is not None:
            if attrs.get("is_technical") != qf.is_technical:
                continue

        # location_contains — check founder.location + attributes.location + intake.location_hint
        if qf.location_contains:
            needle = qf.location_contains.lower()
            candidates = [
                founder.location or "",
                (attrs.get("location") or "") if isinstance(attrs, dict) else "",
                ((founder.intake or {}).get("location_hint") or "")
                if isinstance(founder.intake, dict)
                else "",
            ]
            if not any(needle in c.lower() for c in candidates):
                continue

        # categories_any — intersect with founder.categories
        if qf.categories_any:
            f_cats = [c.lower() for c in (founder.categories or [])]
            wants = [c.lower() for c in qf.categories_any]
            if not any(any(w in fc or fc in w for fc in f_cats) for w in wants):
                continue

        # customer_segment
        if qf.customer_segment is not None:
            if attrs.get("customer_segment") != qf.customer_segment:
                continue

        # prior_vc_backing
        if qf.prior_vc_backing is not None:
            if attrs.get("prior_vc_backing") != qf.prior_vc_backing:
                continue

        # accelerator_tier
        if qf.accelerator_tier is not None:
            if attrs.get("accelerator_tier") != qf.accelerator_tier:
                continue

        # min_prior_exits
        if qf.min_prior_exits is not None:
            v = attrs.get("prior_exits") or 0
            if not isinstance(v, int) or v < qf.min_prior_exits:
                continue

        # is_researcher
        if qf.is_researcher is not None:
            if bool(attrs.get("is_researcher")) != qf.is_researcher:
                continue

        # min_h_index
        if qf.min_h_index is not None:
            h = attrs.get("h_index") or 0
            if not isinstance(h, int) or h < qf.min_h_index:
                continue

        filtered.append((founder, score))

    filtered.sort(key=lambda t: (t[1] is None, -(t[1] or 0.0)))
    return [f for f, _ in filtered]