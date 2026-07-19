"""Thesis Engine — flag every founder as in_thesis / outside_thesis with reason.

Never filters — the flag is purely informational, matching the brief:
"every recommendation is filtered and scored through this fund-specific lens"
means the flag is visible, not that outsiders are hidden.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ventureos_ui.models_orm import Founder, ThesisConfig, ThesisFit


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


# Very light geography → region map. The thesis stores regions like "US", "EU",
# "Global"; the founder's location is a free-text string. Add more entries
# here rather than in the loader — this is UI-side classification.
_COUNTRY_TO_REGION: dict[str, str] = {
    # US
    "usa": "US", "us": "US", "united states": "US",
    "california": "US", "san francisco": "US", "sf": "US", "new york": "US", "ny": "US",
    "boston": "US", "texas": "US", "seattle": "US", "chicago": "US",
    # EU
    "germany": "EU", "berlin": "EU", "france": "EU", "paris": "EU",
    "netherlands": "EU", "amsterdam": "EU", "spain": "EU", "italy": "EU",
    "poland": "EU", "sweden": "EU", "denmark": "EU", "ireland": "EU",
    "portugal": "EU", "belgium": "EU", "finland": "EU",
    # UK is often treated as EU-adjacent for pre-seed
    "united kingdom": "UK", "uk": "UK", "london": "UK",
    # APAC
    "singapore": "APAC", "india": "APAC", "japan": "APAC", "tokyo": "APAC",
}


def _classify_region(location: str | None) -> str | None:
    if not location:
        return None
    loc = location.lower()
    for key, region in _COUNTRY_TO_REGION.items():
        if key in loc:
            return region
    return None


def _sector_matches(founder_categories: list[str], thesis_sectors: list[str]) -> bool:
    """Case-insensitive substring match — 'AI infra' matches 'ai infrastructure'.

    Empty founder categories → "unknown", not "no match". Consistent with the
    pipeline's empty-results-are-evidence invariant: absence of data must not
    silently classify a founder as outside-thesis.
    """
    if not thesis_sectors:
        return True  # empty sector list = wide open
    if not founder_categories:
        return True  # unknown → allow through (reason string will note it)
    f_lower = [c.lower() for c in founder_categories]
    t_lower = [s.lower() for s in thesis_sectors]
    for f in f_lower:
        for t in t_lower:
            if t in f or f in t:
                return True
    return False


def _geography_matches(location: str | None, thesis_geography: list[str]) -> bool:
    """`Global` in thesis geography = matches everything."""
    if not thesis_geography:
        return True
    if any(g.lower() == "global" for g in thesis_geography):
        return True
    if location is None:
        # No location evidence at all — allow through but the reason will note it
        return True
    region = _classify_region(location)
    if region is None:
        return True  # unknown region — don't punish
    return any(g.upper() == region for g in thesis_geography)


# --------------------------------------------------------------------------- #
# Public entrypoint                                                           #
# --------------------------------------------------------------------------- #


def compute_and_persist(session: Session, founder_id: str) -> ThesisFit:
    thesis = session.get(ThesisConfig, "current")
    founder = session.get(Founder, founder_id)
    if not thesis or not founder:
        # Persist a permissive default rather than skipping
        row = session.get(ThesisFit, founder_id)
        if row is None:
            row = ThesisFit(founder_id=founder_id)
            session.add(row)
        row.thesis_fit = "in_thesis"
        row.reason = "No thesis config or founder record; defaulting to in_thesis."
        return row

    attrs: dict[str, Any] = founder.attributes or {}
    intake: dict[str, Any] = founder.intake or {}

    # Founder categories: prefer explicit founder.categories, then rolled-up
    # attributes.categories, then intake.category_labels (highest recall).
    founder_categories: list[str] = (
        list(founder.categories or [])
        or list(attrs.get("categories") or [])
        or list(intake.get("category_labels") or [])
    )
    founder_location = (
        founder.location
        or (attrs.get("location") if isinstance(attrs, dict) else None)
        or (intake.get("location_hint") if isinstance(intake, dict) else None)
    )

    mismatches: list[str] = []
    matches: list[str] = []

    # Sector
    if _sector_matches(founder_categories, thesis.sectors):
        matches.append(f"sector: {founder_categories or '[unspecified]'}")
    else:
        mismatches.append(
            f"sector: {founder_categories or '[unspecified]'} not in thesis {thesis.sectors}"
        )

    # Geography
    if _geography_matches(founder_location, thesis.geography):
        matches.append(f"geography: {founder_location or 'unspecified'}")
    else:
        mismatches.append(
            f"geography: {founder_location} not in thesis {thesis.geography}"
        )

    # Stage is not yet extracted per-founder; skip until intake produces stage.
    # (Structural TODO — leave a note in the reason if we ever tighten this.)

    status = "outside_thesis" if mismatches else "in_thesis"
    reason = ("; ".join(mismatches) if mismatches else "; ".join(matches)) or "no criteria checked"

    row = session.get(ThesisFit, founder_id)
    if row is None:
        row = ThesisFit(founder_id=founder_id)
        session.add(row)
    row.thesis_fit = status
    row.reason = reason
    session.flush()
    return row


def recompute_all(session: Session) -> int:
    """Recompute thesis fit for every founder. Called when the thesis config
    is edited in the Streamlit sidebar."""
    from sqlalchemy import select

    ids = list(session.execute(select(Founder.id)).scalars())
    for fid in ids:
        compute_and_persist(session, fid)
    session.commit()
    return len(ids)