"""
Dashboard skeleton -- Phase 2, Person B.

Run with:
    streamlit run app.py

What this does right now:
  - Sidebar: Thesis Config, live-writing to thesis_config on every
    change (no save button -- Streamlit reruns the script top-to-bottom
    on any widget interaction, so we just upsert on every run).
  - Main area: 5 tabs -- Overview (lists founders from the DB, this is
    what the 11:30 AM meeting point checks), Founder, Market, SWOT,
    Memo -- the last 4 are placeholders until Phase 4-6 wire real data in.

Nothing here is scoring/sourcing logic -- it only reads/writes through
crud.py, which is what keeps this file safe to build in parallel with
Person A's pipeline work.
"""

from pathlib import Path
import sys

import streamlit as st

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import crud

st.set_page_config(page_title="VC Brain", layout="wide")


# ============================================================
# Sidebar -- Thesis Config
# ============================================================

def render_thesis_sidebar():
    st.sidebar.header("Thesis Config")
    st.sidebar.caption("Changes apply instantly -- no save button.")

    existing = crud.get_thesis_config()

    # Pre-fill widgets with whatever's already saved, so the sidebar
    # doesn't reset to defaults every time the app reruns or reloads.
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

    # Write on every rerun -- cheap upsert, and this is exactly what
    # makes the "change geography live, watch badges update" demo
    # moment work later once opportunity cards read this config.
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
# Tab: Overview -- what the 11:30 AM meeting point checks
# ============================================================

def render_overview_tab():
    st.subheader("Sourced Founders")

    founders = crud.get_all_founders()

    if not founders:
        st.info("No founders yet. Once Person A's Sourcing/Screening node runs, they'll show up here.")
        return

    for f in founders:
        with st.container(border=True):
            cols = st.columns([3, 2, 2, 2])
            cols[0].markdown(f"**{f.name}**")
            cols[0].caption(f.bio or "No bio available")
            cols[1].metric("Founder Score", f"{f.founder_score:.0f}" if f.founder_score else "—")
            cols[2].write(f"Confidence: {f.founder_score_confidence or '—'}")
            cols[3].write(f"Source: {f.source_type or '—'}")


# ============================================================
# Placeholder tabs -- Phases 4-6 fill these in
# ============================================================

def render_founder_tab():
    st.subheader("Founder Profile")
    st.caption("Placeholder -- score, confidence range, history chart, and tier breakdown land here in Phase 4/7.")

    founders = crud.get_all_founders()
    if founders:
        names = [f.name for f in founders]
        selected = st.selectbox("Select a founder", names)
        st.info(f"Detail view for **{selected}** goes here once scoring is wired in.")
    else:
        st.info("No founders yet.")


def render_market_tab():
    st.subheader("Market Research")
    st.caption("Placeholder -- category, competitors, sizing, bull/neutral/bear synthesis land here in Phase 5.")


def render_swot_tab():
    st.subheader("SWOT Analysis")
    st.caption("Placeholder -- derived view over Founder evidence + Market gaps + contradictions, Phase 6.")


def render_memo_tab():
    st.subheader("Investment Memo")
    st.caption("Placeholder -- assembled memo with inline Trust Scores and '[Not Disclosed]' fields, Phase 6.")


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