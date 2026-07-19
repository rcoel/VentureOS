"""Investment Memo — rendered Markdown + Markdown download."""

from __future__ import annotations

import streamlit as st
from sqlalchemy import select

from ventureos_ui.db import get_session
from ventureos_ui.memo.memo_builder import regenerate as regenerate_memo
from ventureos_ui.models_orm import Founder, Memo
from ventureos_ui.ui_helpers import (
    bootstrap,
    get_selected_founder,
    render_sidebar_data_ops,
    render_sidebar_thesis_editor,
    set_selected_founder,
)

st.set_page_config(page_title="Memo · VentureOS", page_icon="📄", layout="wide")
bootstrap()
render_sidebar_thesis_editor()
render_sidebar_data_ops()


with get_session() as s:
    all_founders = [
        (f.id, f"{f.company or '(no company)'} — {f.founder_name or '?'}")
        for f in s.execute(select(Founder).order_by(Founder.company)).scalars()
    ]

if not all_founders:
    st.warning("No founders in the DB yet. Click **Reload demo_data/*** in the sidebar.")
    st.stop()

selected = get_selected_founder()
if selected not in {fid for fid, _ in all_founders}:
    selected = all_founders[0][0]
    set_selected_founder(selected)

picker = st.selectbox(
    "Founder",
    options=[fid for fid, _ in all_founders],
    format_func=lambda fid: dict(all_founders)[fid],
    index=[fid for fid, _ in all_founders].index(selected),
)
if picker != selected:
    set_selected_founder(picker)
    selected = picker

col_a, col_b, col_c = st.columns([1, 1, 6])
regen = col_a.button("↻ Regenerate memo")

with get_session() as s:
    if regen:
        regenerate_memo(s, selected)
        s.commit()
        st.success("Memo regenerated.")
    memo = s.get(Memo, selected)
    founder = s.get(Founder, selected)

if memo is None:
    st.info("No memo yet. Click **Regenerate memo**.")
    st.stop()

slug = (founder.company or "founder").lower().replace(" ", "_")
col_b.download_button(
    "Download .md",
    data=memo.markdown,
    file_name=f"memo_{slug}.md",
    mime="text/markdown",
)

st.markdown(memo.markdown, unsafe_allow_html=False)