"""Load pipeline JSON output into the DB.

Consumes the two file formats produced by the pipeline:
1. Inbound run: `scripts/run_pipeline.py --out demo_data/x.json`
   → top-level keys are the GraphState (founder_name, company, ...).
2. Outbound scan: `scripts/outbound_scan.py --out-dir demo_data/outbound/`
   → wrapper with {candidate_source, reference_url, devpost_extras, final_state}.

Idempotent: reloading the same file leaves the DB in the same state (upserts
by primary key). ScoreHistory is the one exception — every load appends a
new row so the demo chart shows an evolving score.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ventureos_ui.db import get_session, init_db
from ventureos_ui.models_orm import (
    AxisScore,
    Claim,
    Contradiction,
    EvidenceItem,
    Founder,
    FounderScore,
    MarketResearch,
    Memo,
    ScoreHistory,
    SWOTEntry,
    ThesisConfig,
    ThesisFit,
)
from ventureos_ui.scoring.trust_score import compute_trust_score

log = logging.getLogger("ventureos_ui.loader")


# --------------------------------------------------------------------------- #
# Utilities                                                                   #
# --------------------------------------------------------------------------- #


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def make_founder_id(founder_name: str, company: str) -> str:
    """Stable slug used as Founder.id. Idempotent across reloads."""
    combined = f"{founder_name or 'unknown'}-{company or 'unknown'}".lower()
    slug = _SLUG_RE.sub("-", combined).strip("-")
    return slug[:120] or "unknown"


def _parse_dt(value: Any) -> datetime:
    """Coerce a datetime string or None to a UTC datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _extract_final_state(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (final_state, wrapper_meta).

    wrapper_meta carries candidate_source / reference_url / devpost_extras
    from outbound wrappers, and is empty for inbound runs.
    """
    if "final_state" in payload and isinstance(payload["final_state"], dict):
        return payload["final_state"], {
            "candidate_source": payload.get("candidate_source"),
            "reference_url": payload.get("reference_url"),
            "devpost_extras": payload.get("devpost_extras"),
        }
    return payload, {}


# --------------------------------------------------------------------------- #
# Per-entity upserts                                                          #
# --------------------------------------------------------------------------- #


def _upsert_founder(
    session: Session,
    founder_id: str,
    state: dict[str, Any],
    meta: dict[str, Any],
) -> Founder:
    attrs = state.get("attributes") or {}
    intake = state.get("intake") or {}
    source = meta.get("candidate_source") or ("outbound" if state.get("is_outbound") else "inbound")

    row = session.get(Founder, founder_id)
    if row is None:
        row = Founder(id=founder_id)
        session.add(row)

    row.founder_name = state.get("founder_name") or ""
    row.company = state.get("company") or ""
    row.is_outbound = bool(state.get("is_outbound", False))
    row.source = source
    row.reference_url = meta.get("reference_url")
    row.location = (attrs or {}).get("location") if isinstance(attrs, dict) else None
    row.categories = list(attrs.get("categories") or []) if isinstance(attrs, dict) else []
    row.attributes = attrs if isinstance(attrs, dict) else {}
    row.intake = intake if isinstance(intake, dict) else {}
    row.devpost_extras = meta.get("devpost_extras")
    row.outreach_draft = state.get("outreach_draft")
    row.screen_status = state.get("screen_status") or "PENDING"
    row.screen_reason = state.get("screen_reason") or ""

    # Agent trace — captured verbatim from the pipeline
    row.reasoning_log = list(state.get("reasoning_log") or [])
    row.trace = list(state.get("trace") or [])
    row.errors = list(state.get("errors") or [])
    row.preliminary_score = float(state.get("preliminary_score") or 0.0)
    return row


def _upsert_evidence(session: Session, founder_id: str, evidence: list[dict[str, Any]]) -> None:
    for ev in evidence or []:
        ev_id = ev.get("id")
        if not ev_id:
            continue
        row = session.get(EvidenceItem, ev_id)
        if row is None:
            row = EvidenceItem(id=ev_id, founder_id=founder_id)
            session.add(row)
        row.founder_id = founder_id
        row.source_type = ev.get("source_type") or "unknown"
        row.source_url = ev.get("source_url")
        row.raw_content = ev.get("raw_content") or {}
        row.query_used = ev.get("query_used") or ""
        row.fetched_at = _parse_dt(ev.get("fetched_at"))
        row.status = ev.get("status") or "ok"


def _upsert_claims(
    session: Session,
    founder_id: str,
    claims: list[dict[str, Any]],
    verification_map: dict[str, str],
) -> None:
    for c in claims or []:
        cid = c.get("id")
        if not cid:
            continue
        row = session.get(Claim, cid)
        if row is None:
            row = Claim(id=cid, founder_id=founder_id)
            session.add(row)
        row.founder_id = founder_id
        row.source_evidence_id = c.get("source_evidence_id") or ""
        row.text = c.get("text") or ""
        row.subject = c.get("subject") or "founder"
        row.predicate = c.get("predicate") or "unknown"
        row.value = c.get("value") or ""
        row.confidence = float(c.get("confidence") or 0.5)
        row.source_type = c.get("source_type") or "unknown"
        row.verification_status = verification_map.get(cid, "unverifiable")
        row.trust_score = compute_trust_score(row.source_type, row.verification_status)


def _upsert_contradictions(
    session: Session, founder_id: str, contradictions: list[dict[str, Any]]
) -> None:
    # Simple approach: wipe founder's contradictions, re-insert. Contradictions
    # don't have stable IDs across pipeline runs.
    session.query(Contradiction).filter(Contradiction.founder_id == founder_id).delete()
    for c in contradictions or []:
        session.add(
            Contradiction(
                founder_id=founder_id,
                claim_id_a=c.get("claim_id_a") or "",
                claim_id_b=c.get("claim_id_b") or "",
                description=c.get("description") or "",
                predicate=c.get("predicate") or "unknown",
            )
        )


def _upsert_market_research(
    session: Session, founder_id: str, mr: dict[str, Any] | None
) -> None:
    if not mr:
        return
    row = session.get(MarketResearch, founder_id)
    if row is None:
        row = MarketResearch(founder_id=founder_id)
        session.add(row)
    row.competitors = mr.get("competitors") or []
    row.market_size_estimate = mr.get("market_size_estimate")
    row.stance = mr.get("stance") or "neutral"
    row.reasoning = mr.get("reasoning") or ""
    row.evidence_refs = list(mr.get("evidence_refs") or [])


def _upsert_swot(
    session: Session, founder_id: str, swot: dict[str, Any] | None
) -> None:
    """Replace all SWOT entries for a founder with the fresh set."""
    if not swot:
        return
    # Wipe existing entries — SWOT bullets are regenerated wholesale on each pipeline run
    session.query(SWOTEntry).filter(SWOTEntry.founder_id == founder_id).delete()
    for quadrant in ("strengths", "weaknesses", "opportunities", "threats"):
        bullets = swot.get(quadrant) or []
        # Store as singular (strength/weakness/opportunity/threat)
        singular = {
            "strengths": "strength", "weaknesses": "weakness",
            "opportunities": "opportunity", "threats": "threat",
        }[quadrant]
        for b in bullets:
            if not isinstance(b, dict):
                continue
            text = (b.get("text") or "").strip()
            if not text:
                continue
            session.add(
                SWOTEntry(
                    founder_id=founder_id,
                    quadrant=singular,
                    text=text,
                    source_url=b.get("source_url"),
                    source_title=b.get("source_title"),
                    reasoning=b.get("reasoning") or "",
                )
            )


# --------------------------------------------------------------------------- #
# Public entrypoint                                                           #
# --------------------------------------------------------------------------- #


def load_founder_json(path: Path, session: Session | None = None) -> str:
    """Load one pipeline JSON file into the DB. Returns the founder_id."""
    payload = json.loads(Path(path).read_text())
    state, meta = _extract_final_state(payload)

    founder_id = make_founder_id(state.get("founder_name", ""), state.get("company", ""))

    owns_session = session is None
    session = session or get_session()
    try:
        _upsert_founder(session, founder_id, state, meta)
        _upsert_evidence(session, founder_id, state.get("raw_evidence") or [])
        _upsert_claims(
            session,
            founder_id,
            state.get("claims") or [],
            state.get("verification_map") or {},
        )
        _upsert_contradictions(session, founder_id, state.get("contradictions") or [])
        _upsert_market_research(session, founder_id, state.get("market_research"))
        _upsert_swot(session, founder_id, state.get("swot_analysis"))
        session.flush()

        # Run downstream computations
        _compute_and_persist_all(session, founder_id)

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()
    return founder_id


def load_dir(root: Path, session: Session | None = None) -> list[str]:
    """Load every *.json under `root` (recursively, skipping _summary.json)."""
    root = Path(root)
    ids: list[str] = []
    files = sorted(p for p in root.rglob("*.json") if not p.name.startswith("_"))
    for p in files:
        try:
            ids.append(load_founder_json(p, session=session))
            log.info("Loaded %s", p)
        except Exception as e:
            log.warning("Failed to load %s: %s", p, e)
    return ids


# --------------------------------------------------------------------------- #
# Trigger downstream computations                                             #
# --------------------------------------------------------------------------- #


def _compute_and_persist_all(session: Session, founder_id: str) -> None:
    """Fire the scoring / thesis / memo computations for a founder.

    Imported locally to keep loader import cheap and avoid circular imports
    while the scoring modules are being built out.
    """
    from ventureos_ui.scoring.founder_score import compute_and_persist as compute_founder_score
    from ventureos_ui.scoring.axis_scores import compute_and_persist as compute_axes
    from ventureos_ui.scoring.thesis_fit import compute_and_persist as compute_thesis_fit
    from ventureos_ui.memo.memo_builder import regenerate as regenerate_memo

    compute_founder_score(session, founder_id)
    compute_axes(session, founder_id)
    compute_thesis_fit(session, founder_id)
    regenerate_memo(session, founder_id)


# --------------------------------------------------------------------------- #
# Thesis config bootstrap                                                     #
# --------------------------------------------------------------------------- #


def ensure_default_thesis(session: Session) -> None:
    """Create the singleton ThesisConfig row if it doesn't exist."""
    row = session.get(ThesisConfig, "current")
    if row is None:
        row = ThesisConfig(
            id="current",
            sectors=["dev tools", "AI infra"],
            stage="pre-seed",
            geography=["US", "EU"],
            check_size_min=25_000,
            check_size_max=150_000,
            ownership_target=0.05,
            risk_appetite="high",
        )
        session.add(row)
        session.commit()


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Load VentureOS pipeline JSONs into the DB.")
    parser.add_argument("paths", nargs="+", type=Path, help="Files or directories to load.")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables first.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s | %(message)s")

    if args.reset:
        from ventureos_ui.models_orm import Base
        from ventureos_ui.db import get_engine
        Base.metadata.drop_all(get_engine())

    init_db()
    with get_session() as s:
        ensure_default_thesis(s)

    total = 0
    for p in args.paths:
        if p.is_dir():
            total += len(load_dir(p))
        elif p.is_file():
            load_founder_json(p)
            total += 1
        else:
            log.warning("Skipping (not a file or dir): %s", p)
    log.info("Loaded %d founders.", total)


if __name__ == "__main__":
    _cli()