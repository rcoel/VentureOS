"""5-section investment memo assembler.

Deterministic Markdown generation from the DB — no LLM. Every claim
rendered inline with its trust badge and source URL. Missing fields
rendered `[Not Disclosed]`, not omitted. Contradictions surfaced at the top.
"""

from __future__ import annotations

from datetime import datetime, timezone
from textwrap import indent
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ventureos_ui.memo.swot import build_swot
from ventureos_ui.models_orm import (
    AxisScore,
    Claim,
    Contradiction,
    EvidenceItem,
    Founder,
    FounderScore,
    MarketResearch,
    Memo,
    ThesisFit,
)
from ventureos_ui.scoring.constants import (
    EXECUTION_SIGNAL_PREDICATES,
    NARRATIVE_QUALITY_PREDICATES,
    TRACK_RECORD_PREDICATES,
)
from ventureos_ui.scoring.trends import trend_arrow
from ventureos_ui.scoring.trust_score import trust_badge

PROMPT_VERSION = "memo-v1"


def _nd(value: Any) -> str:
    """Not-Disclosed helper."""
    if value is None or value == "" or value == []:
        return "[Not Disclosed]"
    return str(value)


def _claim_line(c: Claim, ev_url: str | None = None) -> str:
    src = f" · [{c.source_type}]"
    if ev_url:
        src += f"({ev_url})"
    return f"- {trust_badge(c.trust_score)} {c.text} (trust {c.trust_score:.2f}){src}"


def _group_claims_by_predicate(claims: list[Claim]) -> dict[str, list[Claim]]:
    grouped: dict[str, list[Claim]] = {}
    for c in claims:
        grouped.setdefault(c.predicate, []).append(c)
    return grouped


def build_memo(session: Session, founder_id: str) -> str:
    founder = session.get(Founder, founder_id)
    if founder is None:
        return f"# Founder {founder_id} not found\n"

    fs = session.get(FounderScore, founder_id)
    axes = list(
        session.execute(select(AxisScore).where(AxisScore.founder_id == founder_id)).scalars()
    )
    axes_by = {a.axis: a for a in axes}
    thesis_fit = session.get(ThesisFit, founder_id)
    mr = session.get(MarketResearch, founder_id)

    claims: list[Claim] = list(
        session.execute(select(Claim).where(Claim.founder_id == founder_id)).scalars()
    )
    evidence: list[EvidenceItem] = list(
        session.execute(
            select(EvidenceItem).where(EvidenceItem.founder_id == founder_id)
        ).scalars()
    )
    evidence_by_id = {e.id: e for e in evidence}
    contradictions: list[Contradiction] = list(
        session.execute(
            select(Contradiction).where(Contradiction.founder_id == founder_id)
        ).scalars()
    )

    attrs: dict[str, Any] = founder.attributes or {}
    devpost = founder.devpost_extras or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append(f"# {founder.company or 'Unknown Company'} — Investment Memo")
    thesis_label = thesis_fit.thesis_fit if thesis_fit else "unknown"
    thesis_reason = thesis_fit.reason if thesis_fit else ""
    lines.append(
        f"_Prepared {now}_ · **Thesis:** `{thesis_label}` — {thesis_reason}"
    )
    lines.append("")

    # ---------- 1. Company Snapshot (hide null fields) ----------
    lines.append("## 1. Company Snapshot")
    snapshot_fields: list[tuple[str, Any]] = [
        ("Founder", founder.founder_name),
        ("Company", founder.company),
        ("Location", founder.location or attrs.get("location")),
        ("Category", ", ".join(founder.categories) if founder.categories else None),
        ("Source", founder.source),
        ("Reference URL", founder.reference_url),
        ("Technical founder", attrs.get("is_technical")),
        ("Customer segment", attrs.get("customer_segment")),
        ("Accelerator tier", attrs.get("accelerator_tier")),
        ("Prior VC backing", attrs.get("prior_vc_backing")),
        ("h-index", attrs.get("h_index")),
    ]
    if devpost:
        h_name = devpost.get("hackathon_name")
        prize = devpost.get("prize_or_placement")
        if h_name or prize:
            snapshot_fields.append(("Devpost", f"{prize or '[?]'} @ {h_name or '[?]'}"))

    shown = [(k, v) for k, v in snapshot_fields if v not in (None, "", [])]
    missing = [k for k, v in snapshot_fields if v in (None, "", [])]
    for k, v in shown:
        lines.append(f"- **{k}:** {v}")
    if missing:
        lines.append(f"- _{len(missing)} field(s) not disclosed:_ " + ", ".join(f"`{k}`" for k in missing))
    lines.append("")

    # ---------- 2. Hypotheses — top verified claims ----------
    lines.append("## 2. Hypotheses (Top Verified Signals)")
    verified = [c for c in claims if c.verification_status == "verified"]
    verified.sort(key=lambda c: c.trust_score, reverse=True)
    if verified:
        for c in verified[:5]:
            ev = evidence_by_id.get(c.source_evidence_id)
            ev_url = ev.source_url if ev else None
            lines.append(_claim_line(c, ev_url))
    else:
        lines.append("- [Not Disclosed] — no verified claims yet.")
    lines.append("")

    # ---------- 3. SWOT (citation-backed) ----------
    lines.append("## 3. SWOT (citation-backed)")
    swot = build_swot(session, founder_id)

    def _swot_bullet(item) -> str:
        cite = ""
        if item.source_url:
            label = item.source_title or item.source_url
            cite = f" · [source]({item.source_url})" if len(label) > 60 else f" · [{label}]({item.source_url})"
        elif item.reasoning:
            cite = f" · _{item.reasoning}_"
        return f"- {item.text}{cite}"

    lines.append("### Strengths")
    for s in swot.strengths:
        lines.append(_swot_bullet(s))
    lines.append("### Weaknesses")
    for w in swot.weaknesses:
        lines.append(_swot_bullet(w))
    lines.append("### Opportunities")
    for o in swot.opportunities:
        lines.append(_swot_bullet(o))
    lines.append("### Threats")
    for t in swot.threats:
        lines.append(_swot_bullet(t))
    lines.append("")

    # ---------- 4. Problem / Product ----------
    lines.append("## 4. Problem / Product")
    narrative = [c for c in claims if c.predicate in NARRATIVE_QUALITY_PREDICATES]
    if narrative:
        for c in narrative:
            ev = evidence_by_id.get(c.source_evidence_id)
            ev_url = ev.source_url if ev else None
            lines.append(_claim_line(c, ev_url))
    else:
        lines.append("- [Not Disclosed] — no product / problem claims extracted.")
    lines.append("")

    # ---------- 5. Traction ----------
    lines.append("## 5. Traction")
    lines.append("| Metric | Value | Source | Trust | Verification |")
    lines.append("| --- | --- | --- | --- | --- |")
    traction_preds = EXECUTION_SIGNAL_PREDICATES | {
        "funding_raised", "traction_metric", "revenue", "arr", "mrr",
    }
    traction_rows = [c for c in claims if c.predicate in traction_preds]
    if traction_rows:
        for c in traction_rows[:10]:
            v = c.value or c.text[:60]
            src = c.source_type
            lines.append(
                f"| {c.predicate} | {v} | {src} | "
                f"{trust_badge(c.trust_score)} {c.trust_score:.2f} | "
                f"{c.verification_status} |"
            )
    else:
        lines.append("| [Not Disclosed] | — | — | — | — |")
    lines.append("")

    # ---------- Score breakdown ----------
    lines.append("## Score Breakdown")
    if fs:
        lines.append(
            f"- **Founder Score:** {fs.founder_score} "
            f"(± {fs.confidence_interval_width:.1f})"
            + ("  · **cold-start reweighting applied**" if fs.cold_start_applied else "")
        )
        tr = fs.track_record_component
        ex = fs.execution_signal_component
        nq = fs.narrative_quality_component
        cs = fs.consistency_component
        lines.append(f"  - TrackRecord: {tr if tr is not None else '[Not Disclosed — no evidence]'}")
        lines.append(f"  - ExecutionSignal: {ex if ex is not None else '[Not Disclosed — no evidence]'}")
        lines.append(f"  - NarrativeQuality: {nq}")
        lines.append(f"  - Consistency: {cs}")
        if fs.weights_used:
            w = fs.weights_used
            lines.append(f"  - Weights used: `{w}`")
    else:
        lines.append("- **Founder Score:** [Not Disclosed]")

    # 3-axis
    lines.append("")
    lines.append("### Three-Axis Screening")
    for axis_key, label in [
        ("founder", "Founder"),
        ("market", "Market"),
        ("idea_vs_market", "Idea vs Market"),
    ]:
        a = axes_by.get(axis_key)
        if a:
            lines.append(
                f"- **{label}:** {a.score} · {a.label} · {trend_arrow(a.trend)} {a.trend}"
                f" — {a.reasoning}"
            )
        else:
            lines.append(f"- **{label}:** [Not Disclosed]")
    lines.append("")

    # ---------- Market Research ----------
    lines.append("## Market Research")
    if mr:
        lines.append(f"- **Stance:** {mr.stance}")
        lines.append(f"- **Market Size Estimate:** {_nd(mr.market_size_estimate)}")
        lines.append(f"- **Reasoning:** {mr.reasoning}")
        if mr.competitors:
            lines.append("- **Competitors:**")
            for comp in mr.competitors[:8]:
                name = comp.get("name") if isinstance(comp, dict) else str(comp)
                url = comp.get("url") if isinstance(comp, dict) else None
                one_liner = comp.get("one_liner") if isinstance(comp, dict) else ""
                link = f"[{name}]({url})" if url else name
                lines.append(f"  - {link} — {one_liner}")
    else:
        lines.append("- [Not Disclosed] — market research not yet run.")
    lines.append("")

    # ---------- Activation ----------
    if founder.outreach_draft:
        lines.append("## Draft Outreach (would send)")
        lines.append("```")
        lines.append(founder.outreach_draft)
        lines.append("```")
        lines.append("")

    # ---------- Evidence Manifest ----------
    lines.append("## Evidence Manifest")
    ok_ev = [e for e in evidence if e.status == "ok"]
    distinct_sources = sorted({e.source_type for e in ok_ev})
    lines.append(
        f"- {len(evidence)} evidence items across "
        f"{len(distinct_sources)} sources: {', '.join(distinct_sources) or 'none'}"
    )
    lines.append(f"- {len(claims)} extracted claims · {len(contradictions)} contradictions flagged")
    lines.append("")

    # ---------- Flagged Contradictions (appendix — less prominent) ----------
    if contradictions:
        lines.append("## Flagged Contradictions (appendix)")
        lines.append(
            "_Cross-source disagreements the verification node flagged. Kept here "
            "for auditability; also reflected in the SWOT Threats quadrant._"
        )
        for c in contradictions:
            lines.append(f"- **[{c.predicate}]** {c.description}")
        lines.append("")

    # ---------- Decision Trail (Agentic Traceability) ----------
    reasoning_log = list(founder.reasoning_log or [])
    trace = list(founder.trace or [])
    errors = list(founder.errors or [])
    if reasoning_log or trace or errors:
        lines.append("## Decision Trail")
        lines.append(
            "_Every LangGraph node records why it made its decision. This trail is "
            "captured verbatim from the pipeline run._"
        )
        lines.append("")
        # Map node → duration for inline timing
        dur_by_node = {t.get("node"): t.get("duration_ms") for t in trace}
        for r in reasoning_log:
            node = r.get("node", "?")
            reason = r.get("reason", "")
            dur = dur_by_node.get(node)
            dur_str = f" · `{dur} ms`" if isinstance(dur, (int, float)) else ""
            lines.append(f"- **{node}**{dur_str} — {reason}")
        if errors:
            lines.append("")
            lines.append(f"**Non-fatal errors during run:** {len(errors)}")
            for e in errors[:5]:
                lines.append(f"- [{e.get('node', '?')}] {e.get('error', '')}")

    return "\n".join(lines).strip() + "\n"


def regenerate(session: Session, founder_id: str) -> Memo:
    """Rebuild and upsert the Memo row."""
    markdown = build_memo(session, founder_id)
    row = session.get(Memo, founder_id)
    if row is None:
        row = Memo(founder_id=founder_id)
        session.add(row)
    row.markdown = markdown
    row.prompt_version = PROMPT_VERSION
    session.flush()
    return row