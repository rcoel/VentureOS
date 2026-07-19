"""Natural-language query bar — parse to QueryFilter, run against DB."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ventureos_ui.db import get_session
from ventureos_ui.memo.query_parser import apply_filter, parse_query
from ventureos_ui.models_orm import FounderScore, ThesisFit
from ventureos_ui.ui_helpers import (
    bootstrap,
    render_sidebar_data_ops,
    render_sidebar_thesis_editor,
    set_selected_founder,
)

st.set_page_config(page_title="Query · VentureOS", page_icon="🔎", layout="wide")
bootstrap()
render_sidebar_thesis_editor()
render_sidebar_data_ops()

st.title("Multi-attribute query")
st.caption(
    "Ask in plain English. The query is parsed into a typed `QueryFilter`, "
    "then applied to the DB. Nothing gets filtered by keyword — every field "
    "corresponds to a structured attribute of the founder."
)

EXAMPLES = [
    "technical founder building dev tools",
    "technical founder in Berlin working on AI infra, enterprise traction, no prior VC backing",
    "researcher founder with h-index above 5",
    "YC-backed founder with prior exit",
    "consumer app founder in SF",
]

with st.expander("Example queries", expanded=False):
    for ex in EXAMPLES:
        if st.button(ex, key=f"ex_{ex}"):
            st.session_state["query_input"] = ex

query_text = st.text_input(
    "Your query",
    value=st.session_state.get("query_input", ""),
    placeholder='e.g. "technical founder in Berlin building AI infra"',
    key="query_input",
)

if not query_text.strip():
    st.info("Enter a query above or pick an example.")
    st.stop()

with st.spinner("Parsing query…"):
    qf = parse_query(query_text)

# Show parsed filter as pill badges — makes the interpretation visible
st.markdown("#### Parsed filter")
pills = []
if qf.is_technical is not None:
    pills.append(f"`is_technical = {qf.is_technical}`")
if qf.location_contains:
    pills.append(f"`location contains '{qf.location_contains}'`")
if qf.categories_any:
    pills.append(f"`categories ∈ {qf.categories_any}`")
if qf.customer_segment:
    pills.append(f"`customer = {qf.customer_segment}`")
if qf.prior_vc_backing is not None:
    pills.append(f"`prior_vc_backing = {qf.prior_vc_backing}`")
if qf.accelerator_tier:
    pills.append(f"`accelerator = {qf.accelerator_tier}`")
if qf.min_prior_exits is not None:
    pills.append(f"`prior_exits ≥ {qf.min_prior_exits}`")
if qf.is_researcher is not None:
    pills.append(f"`is_researcher = {qf.is_researcher}`")
if qf.min_h_index is not None:
    pills.append(f"`h_index ≥ {qf.min_h_index}`")

if pills:
    st.markdown("  ".join(pills))
else:
    st.warning("The parser didn't extract any structured constraints. Try being more specific.")

# --------------------------------------------------------------------------- #
# Run filter                                                                  #
# --------------------------------------------------------------------------- #

with get_session() as s:
    matches = apply_filter(s, qf)
    rows = []
    for f in matches:
        fs = s.get(FounderScore, f.id)
        tf = s.get(ThesisFit, f.id)
        rows.append(
            {
                "id": f.id,
                "Company": f.company or "—",
                "Founder": f.founder_name or "—",
                "Score": fs.founder_score if fs else None,
                "Thesis": tf.thesis_fit if tf else "—",
                "Categories": ", ".join(f.categories or []) or "—",
                "Location": f.location or (f.attributes or {}).get("location") or "—",
                "Source": f.source,
            }
        )

st.markdown(f"#### Results: {len(rows)} matching founder(s)")
if not rows:
    st.info("No founders matched. Try a broader query.")
    st.stop()

df = pd.DataFrame(rows)
display_cols = [c for c in df.columns if c != "id"]
event = st.dataframe(
    df[display_cols],
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={"Score": st.column_config.NumberColumn(format="%.1f")},
)

sel_rows = (event.selection or {}).get("rows", []) if event else []
if sel_rows:
    row_ix = sel_rows[0]
    founder_id = df.iloc[row_ix]["id"]
    set_selected_founder(founder_id)
    st.page_link("pages/02_Founder_Profile.py", label="→ Open Founder Profile", icon="👤")