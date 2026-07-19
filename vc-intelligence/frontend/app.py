"""
Dashboard skeleton -- Phase 2/4, Person B.

Run with:
    streamlit run app.py

What this does right now:
  - Sidebar: Thesis Config, live-writing to thesis_config on every
    change (no save button -- Streamlit reruns the script top-to-bottom
    on any widget interaction, so we just upsert on every run).
  - Overview tab: a "Fetch Founders" button simulates the outbound
    sourcing scan (pipeline.seed_initial_data) and populates the DB.
    Each sourced opportunity shows as a card with an "Analyze" button.
    Clicking Analyze calls pipeline.run_analysis(opportunity_id), which
    SIMULATES the LLM/scoring pipeline and writes real values into the
    DB -- then the page reruns and shows the updated columns.
  - Founder / Market / SWOT / Memo tabs: now wired up. Each lets you
    pick any *analyzed* opportunity (selection is shared across tabs
    via session_state) and renders detail data from pipeline.py's
    mock generators (get_founder_profile / get_market_research /
    get_swot_analysis / get_memo). These are read-only mocks for now
    -- swap their bodies in pipeline.py for real LLM calls later,
    this file won't need to change.

Nothing here talks to SQLAlchemy directly -- everything goes through
crud.py, and the simulated pipeline logic lives in pipeline.py so it
can be swapped for Person A's real LangGraph call later without
touching this file.
"""

from pathlib import Path
import sys

import streamlit as st

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import crud, pipeline

st.set_page_config(page_title="VC Brain", layout="wide")

# ============================================================
# Sidebar -- Thesis Config
# ============================================================

def render_thesis_sidebar():
    st.sidebar.header("Thesis Config")
    st.sidebar.caption("Changes apply instantly -- no save button.")

    existing = crud.get_thesis_config()

    available_sectors = ["AI Infra", "AI Applications", "Fintech", "Healthtech",
                         "Devtools", "Climate", "Consumer", "Enterprise SaaS", "Other"]
    default_sectors = existing.sectors if existing and existing.sectors else []
    default_sectors = [s for s in default_sectors if s in available_sectors]
    default_stage = existing.stage if existing else "Pre-seed"
    default_geography = existing.geography if existing else ""
    default_check_min = existing.check_size_min if existing and existing.check_size_min else 100_000
    default_check_max = existing.check_size_max if existing and existing.check_size_max else 1_000_000
    default_ownership = existing.ownership_target if existing and existing.ownership_target else 10.0
    default_risk = existing.risk_appetite if existing else "Medium"

    sectors = st.sidebar.multiselect(
        "Sectors",
        options=available_sectors,
        default=default_sectors,
    )

    stage = st.sidebar.selectbox(
        "Stage",
        options=["Pre-seed", "Seed", "Series A", "Series B+"],
        index=["Pre-seed", "Seed", "Series A", "Series B+"].index(default_stage)
        if default_stage in ["Pre-seed", "Seed", "Series A", "Series B+"] else 0,
    )

    geography = st.sidebar.text_input(
        "Geography",
        value=default_geography,
        placeholder="e.g. US, EU or Global",
    )

    col1, col2 = st.sidebar.columns(2)
    check_min = col1.number_input("Check size min ($)", min_value=0, value=int(default_check_min), step=50_000)
    check_max = col2.number_input("Check size max ($)", min_value=0, value=int(default_check_max), step=50_000)

    ownership = st.sidebar.number_input(
        "Ownership target (%)",
        min_value=0.0, max_value=100.0,
        value=float(default_ownership), step=0.5,
    )

    risk = st.sidebar.select_slider(
        "Risk appetite",
        options=["Low", "Medium", "High"],
        value=default_risk if default_risk in ["Low", "Medium", "High"] else "Medium",
    )

    crud.upsert_thesis_config(
        sectors=sectors,
        stage=stage,
        geography=geography,
        check_size_min=check_min,
        check_size_max=check_max,
        ownership_target=ownership,
        risk_appetite=risk,
    )

# ============================================================
# Shared helpers
# ============================================================

def get_founder_display_name(opp):
    if not opp.founder_id:
        return "Unknown founder"

    try:
        founder = opp.founder
    except Exception:
        founder = None

    if founder is not None:
        return founder.name

    fallback = crud.get_founder(opp.founder_id)
    return fallback.name if fallback else "Unknown founder"


def get_analyzed_opportunities():
    return [o for o in crud.get_all_opportunities() if o.screen_status not in (None, "pending")]


def opportunity_selector(tab_key: str):
    """Renders a selectbox of analyzed opportunities. Selection is
    shared across tabs via st.session_state['selected_opportunity_id'],
    so switching tabs keeps you looking at the same company."""
    opportunities = get_analyzed_opportunities()
    if not opportunities:
        st.info("No analyzed opportunities yet. Click **Analyze** on a card in Overview first.")
        return None

    labels = [f"{o.company_name} — {get_founder_display_name(o)}" for o in opportunities]
    ids = [o.id for o in opportunities]

    default_index = 0
    remembered_id = st.session_state.get("selected_opportunity_id")
    if remembered_id in ids:
        default_index = ids.index(remembered_id)

    idx = st.selectbox(
        "Select an opportunity",
        options=range(len(labels)),
        format_func=lambda i: labels[i],
        index=default_index,
        key=f"opp_select_{tab_key}",
    )
    selected = opportunities[idx]
    st.session_state["selected_opportunity_id"] = selected.id
    return selected

# ============================================================
# Tab: Overview -- sourcing + per-opportunity Analyze button
# ============================================================

def render_overview_tab():
    st.subheader("Sourced Opportunities")

    top_cols = st.columns([1, 3])
    with top_cols[0]:
        if st.button("🔎 Fetch Founders", use_container_width=True):
            with st.spinner("Scanning sources..."):
                new_ones = pipeline.seed_initial_data()
            if new_ones:
                st.success(f"Sourced {len(new_ones)} new opportunit{'y' if len(new_ones)==1 else 'ies'}.")
            else:
                st.info("Nothing new -- demo founders already sourced.")
            st.rerun()

    opportunities = crud.get_all_opportunities()

    if not opportunities:
        st.info("No opportunities yet. Click **Fetch Founders** to simulate an outbound scan.")
        return

    for opp in opportunities:
        with st.container(border=True):
            header_cols = st.columns([3, 2, 2, 1.5, 1.5])
            header_cols[0].markdown(f"**{opp.company_name}**")
            founder_name = get_founder_display_name(opp)
            header_cols[0].caption(
                f"{founder_name} · {opp.sector or '—'} · {opp.stage or '—'}"
            )

            analyzed = opp.screen_status not in (None, "pending")

            if not analyzed:
                header_cols[3].markdown("🕗 *Not yet analyzed*")
            else:
                badge = "✅ Passed" if opp.screen_status == "passed" else "🚫 Screened out"
                header_cols[1].write(badge)
                thesis_badge = "🎯 In thesis" if opp.thesis_status == "in_thesis" else "↗️ Outside thesis"
                header_cols[2].write(thesis_badge)

            with header_cols[3]:
                button_label = "🔁 Re-analyze" if analyzed else "▶️ Analyze"
                if st.button(button_label, key=f"analyze_{opp.id}"):
                    with st.spinner(f"Analyzing {opp.company_name}..."):
                        pipeline.run_analysis(opp.id)
                    st.session_state["selected_opportunity_id"] = opp.id
                    st.rerun()

            # Two-step delete: first click arms confirmation, second
            # click (a different button, shown in place of the first)
            # actually deletes. Prevents wiping a demo founder on a
            # stray click.
            confirm_key = f"confirm_delete_{opp.id}"
            with header_cols[4]:
                if st.session_state.get(confirm_key):
                    if st.button("⚠️ Confirm", key=f"confirm_btn_{opp.id}"):
                        if opp.founder_id:
                            crud.delete_founder(opp.founder_id)  # cascades to opportunity, evidence, score history
                        else:
                            crud.delete_opportunity(opp.id)
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                else:
                    if st.button("🗑️ Delete", key=f"delete_btn_{opp.id}"):
                        st.session_state[confirm_key] = True
                        st.rerun()

            if analyzed:
                score_cols = st.columns(4)
                score_cols[0].metric("Founder", f"{opp.founder_score:.0f}" if opp.founder_score else "—")
                score_cols[1].metric("Market", f"{opp.market_score:.0f}" if opp.market_score else "—")
                score_cols[2].metric("Product", f"{opp.product_score:.0f}" if opp.product_score else "—")
                score_cols[3].metric("Confidence", f"{opp.confidence_score:.0f}" if opp.confidence_score else "—")

                with st.expander("📋 Full analysis", expanded=False):
                    render_opportunity_detail(opp)


def render_opportunity_detail(opp):
    """Renders the full analysis for one opportunity -- Founder,
    Market, SWOT, Memo -- inline under its card in Overview. This is
    the single place all the detail tabs pull from; it's just a
    function, not a page, so it can be reused anywhere (e.g. if you
    later want a standalone /opportunity/{id} view)."""
    if opp.thesis_reason:
        st.caption(f"Thesis: {opp.thesis_reason}")

    tab_founder, tab_market, tab_swot, tab_memo = st.tabs(
        ["👤 Founder", "📊 Market", "🔍 SWOT", "📝 Memo"]
    )

    with tab_founder:
        profile = pipeline.get_founder_profile(opp.id)
        st.markdown(f"**{profile['name']}**")
        st.write(profile["background"])
        col1, col2 = st.columns(2)
        col1.markdown(f"**Education**  \n{profile['education']}")
        col2.markdown(f"**Prior companies**  \n{', '.join(profile['prior_companies'])}")
        st.markdown(f"**Network signal**  \n{profile['network_signal']}")
        for flag in profile["risk_flags"]:
            st.warning(flag)
        st.caption(f"Scoring note: {profile['tier_note']}")

    with tab_market:
        research = pipeline.get_market_research(opp.id)
        m1, m2, m3 = st.columns(3)
        m1.metric("TAM", f"${research['tam_billion_usd']}B")
        m2.metric("SAM", f"${research['sam_billion_usd']}B")
        m3.metric("Growth", f"{research['growth_rate_pct']}%/yr")
        st.markdown(f"**Competitors**  \n{', '.join(research['competitors'])}")
        st.markdown(f"**Differentiation**  \n{research['differentiation']}")
        st.caption(research["market_note"])

    with tab_swot:
        swot = pipeline.get_swot_analysis(opp.id)
        s1, s2 = st.columns(2)
        with s1:
            st.markdown("**💪 Strengths**")
            for item in swot["strengths"] or ["—"]:
                st.write(f"- {item}")
            st.markdown("**🌱 Opportunities**")
            for item in swot["opportunities"] or ["—"]:
                st.write(f"- {item}")
        with s2:
            st.markdown("**⚠️ Weaknesses**")
            for item in swot["weaknesses"] or ["—"]:
                st.write(f"- {item}")
            st.markdown("**🚨 Threats**")
            for item in swot["threats"] or ["—"]:
                st.write(f"- {item}")

    with tab_memo:
        memo = pipeline.get_memo(opp.id)
        st.markdown(memo["memo_md"])
        if memo["outreach_draft"]:
            st.markdown("**Outreach draft**")
            st.text(memo["outreach_draft"])

# ============================================================
# Tab: Founder -- deep-dive on the selected opportunity's founder
# ============================================================

def render_founder_tab():
    st.subheader("Founder Profile")
    opp = opportunity_selector("founder")
    if opp is None:
        return

    profile = pipeline.get_founder_profile(opp.id)

    st.markdown(f"### {profile['name']}")
    st.caption(f"{opp.company_name} · {opp.sector or '—'} · {opp.stage or '—'}")

    st.metric("Founder Score", f"{opp.founder_score:.0f}" if opp.founder_score else "—")

    st.markdown("**Background**")
    st.write(profile["background"])

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Education**")
        st.write(profile["education"])
    with col2:
        st.markdown("**Prior companies**")
        st.write(", ".join(profile["prior_companies"]))

    st.markdown("**Network signal**")
    st.write(profile["network_signal"])

    if profile["risk_flags"]:
        for flag in profile["risk_flags"]:
            st.warning(flag)

    st.caption(f"Scoring note: {profile['tier_note']}")

# ============================================================
# Tab: Market -- sizing + competitive landscape
# ============================================================

def render_market_tab():
    st.subheader("Market Research")
    opp = opportunity_selector("market")
    if opp is None:
        return

    research = pipeline.get_market_research(opp.id)

    st.caption(f"{opp.company_name} · {research['sector'] or '—'}")

    col1, col2, col3 = st.columns(3)
    col1.metric("TAM", f"${research['tam_billion_usd']}B")
    col2.metric("SAM", f"${research['sam_billion_usd']}B")
    col3.metric("Growth rate", f"{research['growth_rate_pct']}%/yr")

    st.markdown("**Competitors**")
    st.write(", ".join(research["competitors"]))

    st.markdown("**Differentiation**")
    st.write(research["differentiation"])

    st.caption(research["market_note"])

# ============================================================
# Tab: SWOT
# ============================================================

def render_swot_tab():
    st.subheader("SWOT Analysis")
    opp = opportunity_selector("swot")
    if opp is None:
        return

    swot = pipeline.get_swot_analysis(opp.id)
    st.caption(opp.company_name)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**💪 Strengths**")
        for item in swot["strengths"] or ["—"]:
            st.write(f"- {item}")
        st.markdown("**🌱 Opportunities**")
        for item in swot["opportunities"] or ["—"]:
            st.write(f"- {item}")
    with col2:
        st.markdown("**⚠️ Weaknesses**")
        for item in swot["weaknesses"] or ["—"]:
            st.write(f"- {item}")
        st.markdown("**🚨 Threats**")
        for item in swot["threats"] or ["—"]:
            st.write(f"- {item}")

# ============================================================
# Tab: Memo
# ============================================================

def render_memo_tab():
    st.subheader("Investment Memo")
    opp = opportunity_selector("memo")
    if opp is None:
        return

    memo = pipeline.get_memo(opp.id)
    st.markdown(memo["memo_md"])

    if memo["outreach_draft"]:
        st.markdown("**Outreach draft**")
        st.text(memo["outreach_draft"])

# ============================================================
# Main
# ============================================================

def main():
    st.title("VC Brain")

    render_thesis_sidebar()

    tab_overview, tab_founder, tab_market, tab_swot, tab_memo = st.tabs(
        ["Overview", "Founder", "Market", "SWOT", "Memo"]
    )

    with tab_overview:
        render_overview_tab()
    with tab_founder:
        render_founder_tab()
    with tab_market:
        render_market_tab()
    with tab_swot:
        render_swot_tab()
    with tab_memo:
        render_memo_tab()

if __name__ == "__main__":
    main()