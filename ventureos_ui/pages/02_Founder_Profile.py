"""Founder profile — score card + 3-axis panel + score-history chart + evidence."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import select

from ventureos_ui.agent_trace_view import render_agent_trace
from ventureos_ui.db import get_session
from ventureos_ui.models_orm import (
    AxisScore,
    Claim,
    Contradiction,
    EvidenceItem,
    Founder,
    FounderScore,
    MarketResearch,
    ScoreHistory,
    ThesisFit,
)
from ventureos_ui.scoring.trends import trend_arrow
from ventureos_ui.scoring.trust_score import trust_badge
from ventureos_ui.ui_helpers import (
    bootstrap,
    get_selected_founder,
    not_disclosed,
    render_sidebar_data_ops,
    render_sidebar_thesis_editor,
    screen_badge_md,
    thesis_badge_md,
)

st.set_page_config(page_title="Founder Profile · VentureOS", page_icon="👤", layout="wide")
bootstrap()
render_sidebar_thesis_editor()
render_sidebar_data_ops()


# --------------------------------------------------------------------------- #
# Founder picker fallback                                                     #
# --------------------------------------------------------------------------- #


with get_session() as s:
    all_founders: list[tuple[str, str]] = [
        (f.id, f"{f.company or '(no company)'} — {f.founder_name or '?'}")
        for f in s.execute(select(Founder).order_by(Founder.company)).scalars()
    ]

if not all_founders:
    st.warning("No founders in the DB yet. Click **Reload demo_data/*** in the sidebar.")
    st.stop()

selected = get_selected_founder()
if selected not in {fid for fid, _ in all_founders}:
    selected = all_founders[0][0]

with st.container():
    picker = st.selectbox(
        "Founder",
        options=[fid for fid, _ in all_founders],
        format_func=lambda fid: dict(all_founders)[fid],
        index=[fid for fid, _ in all_founders].index(selected),
    )
    if picker != selected:
        st.session_state["selected_founder_id"] = picker
        selected = picker


with get_session() as s:
    founder = s.get(Founder, selected)
    fs = s.get(FounderScore, selected)
    tf = s.get(ThesisFit, selected)
    mr = s.get(MarketResearch, selected)
    axes = {
        a.axis: a
        for a in s.execute(select(AxisScore).where(AxisScore.founder_id == selected)).scalars()
    }
    history = list(
        s.execute(
            select(ScoreHistory)
            .where(ScoreHistory.founder_id == selected)
            .order_by(ScoreHistory.computed_at)
        ).scalars()
    )
    claims = list(
        s.execute(select(Claim).where(Claim.founder_id == selected)).scalars()
    )
    evidence = list(
        s.execute(select(EvidenceItem).where(EvidenceItem.founder_id == selected)).scalars()
    )
    contradictions = list(
        s.execute(
            select(Contradiction).where(Contradiction.founder_id == selected)
        ).scalars()
    )

assert founder is not None

# --------------------------------------------------------------------------- #
# Header                                                                      #
# --------------------------------------------------------------------------- #

st.title(f"{founder.company or 'Unknown Company'}")
st.markdown(
    f"**{founder.founder_name or '?'}** · "
    f"{screen_badge_md(founder.screen_status)} · "
    f"{thesis_badge_md(tf.thesis_fit if tf else 'unknown')} · "
    f"source `{founder.source}`"
    + (f" · Devpost: {founder.devpost_extras.get('prize_or_placement')} @ "
       f"{founder.devpost_extras.get('hackathon_name')}"
       if founder.devpost_extras and founder.devpost_extras.get('hackathon_name') else "")
)
if founder.reference_url:
    st.caption(f"Reference: {founder.reference_url}")

# --------------------------------------------------------------------------- #
# Contradictions banner                                                       #
# --------------------------------------------------------------------------- #

if contradictions:
    with st.expander(f"⚠️  {len(contradictions)} flagged contradiction(s)", expanded=True):
        for c in contradictions:
            st.markdown(f"- **[{c.predicate}]** {c.description}")

# --------------------------------------------------------------------------- #
# Score card + 3-axis panel                                                   #
# --------------------------------------------------------------------------- #

st.markdown("### Score")
score_cols = st.columns([2, 1, 1, 1])

with score_cols[0]:
    if fs:
        delta_text = "cold-start reweighting" if fs.cold_start_applied else None
        st.metric(
            "Founder Score",
            value=f"{fs.founder_score:.1f}",
            delta=delta_text,
            delta_color="off",
        )
        st.caption(f"± {fs.confidence_interval_width:.1f} confidence interval")
    else:
        st.metric("Founder Score", value="—")

for col, axis_key, label in zip(
    score_cols[1:],
    ("founder", "market", "idea_vs_market"),
    ("Founder axis", "Market axis", "Idea vs Market"),
):
    a = axes.get(axis_key)
    if a:
        col.metric(label, value=f"{a.score:.1f}", delta=f"{trend_arrow(a.trend)} {a.label}")
    else:
        col.metric(label, value="—")

# --------------------------------------------------------------------------- #
# Component breakdown                                                         #
# --------------------------------------------------------------------------- #

if fs:
    with st.expander("Component breakdown & weights", expanded=False):
        comp_df = pd.DataFrame(
            [
                {
                    "Component": "TrackRecord",
                    "Score": fs.track_record_component,
                    "Weight": (fs.weights_used or {}).get("track_record"),
                },
                {
                    "Component": "ExecutionSignal",
                    "Score": fs.execution_signal_component,
                    "Weight": (fs.weights_used or {}).get("execution_signal"),
                },
                {
                    "Component": "NarrativeQuality",
                    "Score": fs.narrative_quality_component,
                    "Weight": (fs.weights_used or {}).get("narrative_quality"),
                },
                {
                    "Component": "Consistency",
                    "Score": fs.consistency_component,
                    "Weight": (fs.weights_used or {}).get("consistency"),
                },
            ]
        )
        st.dataframe(comp_df, hide_index=True, use_container_width=True)
        if fs.cold_start_applied:
            st.info(
                "**Cold-start reweighting applied.** One or both of TrackRecord / "
                "ExecutionSignal had no supporting evidence, so their weight was "
                "redistributed into NarrativeQuality + Consistency rather than "
                "averaging in zeros. This is the pipeline's fairness rule for "
                "founders without an obvious digital footprint."
            )

# --------------------------------------------------------------------------- #
# Score history chart                                                         #
# --------------------------------------------------------------------------- #

st.markdown("### Score history")
if history:
    hist_df = pd.DataFrame(
        [
            {"computed_at": h.computed_at, "axis": h.axis, "score": h.score}
            for h in history
        ]
    )
    fig = px.line(
        hist_df,
        x="computed_at",
        y="score",
        color="axis",
        markers=True,
        title=None,
    )
    fig.update_layout(
        xaxis_title="Time",
        yaxis_title="Score",
        yaxis=dict(range=[0, 100]),
        height=350,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", y=-0.2),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.caption("No score history yet (score only recorded on load / recompute).")

# --------------------------------------------------------------------------- #
# Attributes                                                                  #
# --------------------------------------------------------------------------- #

st.markdown("### Attributes (typed rollup)")
attrs = founder.attributes or {}
attr_rows = [
    ("Technical founder", attrs.get("is_technical")),
    ("Location", founder.location or attrs.get("location")),
    ("Categories", ", ".join(founder.categories) if founder.categories else None),
    ("Customer segment", attrs.get("customer_segment")),
    ("Prior VC backing", attrs.get("prior_vc_backing")),
    ("Accelerator tier", attrs.get("accelerator_tier")),
    ("Prior exits", attrs.get("prior_exits")),
    ("Years experience", attrs.get("years_experience")),
    ("Researcher", attrs.get("is_researcher")),
    ("h-index", attrs.get("h_index")),
]

# Only show rows that have real values — investors want signal, not gaps
present_rows = [(k, v) for k, v in attr_rows if v not in (None, "", [], False)]
# `is_researcher=False` is meaningful signal though — treat separately
if attrs.get("is_researcher") is False:
    present_rows.append(("Researcher", False))
missing_keys = [k for k, v in attr_rows if v in (None, "", [])]

if present_rows:
    attr_df = pd.DataFrame(
        [{"Attribute": k, "Value": str(v)} for k, v in present_rows]
    )
    st.dataframe(attr_df, hide_index=True, use_container_width=True)
else:
    st.info("No attributes have been extracted yet.")

if missing_keys:
    st.caption(
        f"_{len(missing_keys)} attribute(s) not disclosed:_ "
        + ", ".join(f"`{k}`" for k in missing_keys)
    )

# --------------------------------------------------------------------------- #
# Evidence + claims                                                            #
# --------------------------------------------------------------------------- #

tab_claims, tab_evidence, tab_market, tab_swot, tab_outreach, tab_trace = st.tabs(
    ["Claims", "Evidence sources", "Market research", "🎯 SWOT", "Outreach draft", "🧠 Agent Trace"]
)

with tab_claims:
    if claims:
        # Build a map from evidence_id → URL so we can render clickable links
        ev_url_by_id = {e.id: e.source_url for e in evidence}
        claim_rows = []
        for c in claims:
            claim_rows.append(
                {
                    "Predicate": c.predicate,
                    "Text": c.text,
                    "Value": c.value,
                    "Source": c.source_type,
                    "Verified": c.verification_status,
                    "Trust": f"{trust_badge(c.trust_score)} {c.trust_score:.2f}",
                    "Evidence": ev_url_by_id.get(c.source_evidence_id) or "",
                }
            )
        st.dataframe(
            pd.DataFrame(claim_rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Evidence": st.column_config.LinkColumn(
                    "Evidence",
                    help="Click to open the source page",
                    display_text="↗ open",
                ),
            },
        )
    else:
        st.info("No claims extracted for this founder.")

with tab_evidence:
    ev_rows = []
    for e in evidence:
        ev_rows.append(
            {
                "Source": e.source_type,
                "Status": e.status,
                "Query": e.query_used,
                "URL": e.source_url or "",
                "Fetched at": e.fetched_at,
            }
        )
    if ev_rows:
        st.dataframe(
            pd.DataFrame(ev_rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "URL": st.column_config.LinkColumn(
                    "URL",
                    help="Click to open evidence source",
                    display_text="↗ open",
                ),
                "Fetched at": st.column_config.DatetimeColumn(
                    "Fetched",
                    format="D MMM YYYY, HH:mm",
                ),
            },
        )
    else:
        st.info("No evidence items for this founder.")

with tab_market:
    if mr:
        st.markdown(f"**Stance:** `{mr.stance}`")
        st.markdown(f"**Market size estimate:** {not_disclosed(mr.market_size_estimate)}")
        st.markdown(f"**Reasoning:** {mr.reasoning}")
        if mr.competitors:
            st.markdown("**Competitors:**")
            comp_df = pd.DataFrame(mr.competitors)
            st.dataframe(comp_df, hide_index=True, use_container_width=True)
    else:
        st.info("No market research recorded (pipeline market_research_node returned None).")

with tab_swot:
    st.caption(
        "SWOT bullets are produced by the market_research pipeline node from "
        "4 targeted web queries. Each bullet cites the source that supports it."
    )
    from ventureos_ui.memo.swot import build_swot as _build_swot

    with get_session() as _s_swot:
        _swot = _build_swot(_s_swot, selected)

    def _render_swot_quadrant(title: str, items: list) -> None:
        st.markdown(f"#### {title}")
        if not items:
            st.caption("_[Not Disclosed]_")
            return
        for it in items:
            if it.source_url:
                label = it.source_title or "source"
                if len(label) > 60:
                    label = "source"
                st.markdown(f"- {it.text}  · [🔗 {label}]({it.source_url})")
            elif it.reasoning:
                st.markdown(f"- {it.text}")
                st.caption(f"   _{it.reasoning}_")
            else:
                st.markdown(f"- {it.text}")

    swot_cols = st.columns(2)
    with swot_cols[0]:
        _render_swot_quadrant("🟢 Strengths", _swot.strengths)
        _render_swot_quadrant("🟡 Weaknesses", _swot.weaknesses)
    with swot_cols[1]:
        _render_swot_quadrant("🔵 Opportunities", _swot.opportunities)
        _render_swot_quadrant("🔴 Threats", _swot.threats)

with tab_outreach:
    if founder.outreach_draft:
        st.code(founder.outreach_draft, language="markdown")
    else:
        st.info(
            "No outreach draft. Drafts are only generated for outbound founders "
            "with preliminary_score ≥ 60."
        )

with tab_trace:
    st.markdown(
        "Every node in the pipeline records **why** it made the decision it did. "
        "This tab shows the full reasoning trail, timing, and any non-fatal errors."
    )
    render_agent_trace(
        reasoning_log=founder.reasoning_log,
        trace=founder.trace,
        errors=founder.errors,
    )

# --------------------------------------------------------------------------- #
# Actions                                                                     #
# --------------------------------------------------------------------------- #

st.markdown("---")
c1, c2 = st.columns(2)
with c1:
    if st.button("↻ Recompute score + axes + memo"):
        from ventureos_ui.scoring.axis_scores import compute_and_persist as _axes
        from ventureos_ui.scoring.founder_score import compute_and_persist as _fscore
        from ventureos_ui.scoring.thesis_fit import compute_and_persist as _thesis
        from ventureos_ui.memo.memo_builder import regenerate as _memo

        with get_session() as s:
            _fscore(s, selected)
            _axes(s, selected)
            _thesis(s, selected)
            _memo(s, selected)
            s.commit()
        st.success("Recomputed. History updated.")
        st.rerun()

with c2:
    st.page_link("pages/03_Memo.py", label="→ Open memo", icon="📄")