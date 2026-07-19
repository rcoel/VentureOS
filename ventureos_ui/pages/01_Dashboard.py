"""Dashboard — founder list with score, thesis, screening, source columns."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from ventureos_ui.db import get_session
from ventureos_ui.models_orm import AxisScore, Founder, FounderScore, ThesisFit
from ventureos_ui.scoring.trends import trend_arrow
from ventureos_ui.ui_helpers import (
    bootstrap,
    render_sidebar_data_ops,
    render_sidebar_thesis_editor,
    set_selected_founder,
)

st.set_page_config(page_title="Dashboard · VentureOS", page_icon="🧠", layout="wide")
bootstrap()
render_sidebar_thesis_editor()
render_sidebar_data_ops()

st.title("Founder Dashboard")
st.caption("Every founder the pipeline has processed. Click a row to open the profile.")


def _load_rows(session: Session) -> list[dict]:
    q = (
        select(Founder, FounderScore, ThesisFit)
        .join(FounderScore, FounderScore.founder_id == Founder.id, isouter=True)
        .join(ThesisFit, ThesisFit.founder_id == Founder.id, isouter=True)
    )
    rows: list[dict] = []
    for founder, fs, tf in session.execute(q).all():
        axes = {
            a.axis: a
            for a in session.execute(
                select(AxisScore).where(AxisScore.founder_id == founder.id)
            ).scalars()
        }
        founder_axis = axes.get("founder")
        market_axis = axes.get("market")
        idea_axis = axes.get("idea_vs_market")
        rows.append(
            {
                "id": founder.id,
                "Company": founder.company or "—",
                "Founder": founder.founder_name or "—",
                "Score": fs.founder_score if fs else None,
                "± CI": fs.confidence_interval_width if fs else None,
                "Cold-start": "❄️" if (fs and fs.cold_start_applied) else "",
                "Founder axis": (
                    f"{founder_axis.score:.0f} {trend_arrow(founder_axis.trend)}"
                    if founder_axis
                    else "—"
                ),
                "Market axis": (
                    f"{market_axis.score:.0f} {trend_arrow(market_axis.trend)}"
                    if market_axis
                    else "—"
                ),
                "Idea vs Market": (
                    f"{idea_axis.score:.0f} · {idea_axis.label}"
                    if idea_axis
                    else "—"
                ),
                "Screen": founder.screen_status,
                "Thesis": (tf.thesis_fit if tf else "—"),
                "Source": founder.source,
                "Outreach": "✉️" if founder.outreach_draft else "",
            }
        )
    return rows


with get_session() as s:
    rows = _load_rows(s)

if not rows:
    st.warning("No founders in the DB yet. Click **Reload demo_data/*** in the sidebar.")
    st.stop()

df = pd.DataFrame(rows)
# Hide `id` from view but keep it for lookup after selection
display_cols = [c for c in df.columns if c != "id"]

event = st.dataframe(
    df[display_cols],
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "Score": st.column_config.NumberColumn(format="%.1f"),
        "± CI": st.column_config.NumberColumn(format="%.1f"),
    },
)

sel_rows = (event.selection or {}).get("rows", []) if event else []
if sel_rows:
    row_ix = sel_rows[0]
    founder_id = df.iloc[row_ix]["id"]
    set_selected_founder(founder_id)
    st.success(f"Selected: {df.iloc[row_ix]['Company']} — opening profile…")
    st.page_link("pages/02_Founder_Profile.py", label="→ Open Founder Profile", icon="👤")

st.markdown(
    "**Tips:** the score-color and trend arrows carry meaning — click any row to drill in. "
    "Change the Thesis Engine on the sidebar and the `Thesis` column recomputes live."
)