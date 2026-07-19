"""VentureOS Streamlit app — main entry.

Run with:
    uv run streamlit run ventureos_ui/app.py

Pages live under `ventureos_ui/pages/` and are auto-discovered by Streamlit.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy import func, select

from ventureos_ui.db import get_session
from ventureos_ui.models_orm import (
    Claim,
    Contradiction,
    EvidenceItem,
    Founder,
    FounderScore,
    Memo,
)
from ventureos_ui.ui_helpers import (
    bootstrap,
    render_sidebar_data_ops,
    render_sidebar_thesis_editor,
)

st.set_page_config(
    page_title="VentureOS",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

bootstrap()
render_sidebar_thesis_editor()
render_sidebar_data_ops()


# --------------------------------------------------------------------------- #
# Home                                                                        #
# --------------------------------------------------------------------------- #

st.title("VentureOS")
st.caption("Evidence-backed founder screening — Person B (scoring + UX) view of the LangGraph pipeline output.")

with get_session() as s:
    n_founders = s.scalar(select(func.count()).select_from(Founder)) or 0
    n_evidence = s.scalar(select(func.count()).select_from(EvidenceItem)) or 0
    n_claims = s.scalar(select(func.count()).select_from(Claim)) or 0
    n_contradictions = s.scalar(select(func.count()).select_from(Contradiction)) or 0
    n_memos = s.scalar(select(func.count()).select_from(Memo)) or 0
    avg_score = s.scalar(select(func.avg(FounderScore.founder_score))) or 0.0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Founders", n_founders)
c2.metric("Evidence items", n_evidence)
c3.metric("Claims", n_claims)
c4.metric("Contradictions flagged", n_contradictions)
c5.metric("Avg founder score", f"{avg_score:.1f}")

st.markdown(
    """
### How this app works

1. **Person A's LangGraph pipeline** turns a founder application (or a Devpost/HN/GitHub outbound signal)
   into a structured JSON file under `demo_data/`.
2. **`ventureos_ui.loader`** reads those JSONs into a SQLite DB, computing per-claim trust
   scores, a 4-tier Founder Score (with cold-start reweighting when applicable), a 3-axis screening
   (Founder / Market / Idea-vs-Market — never averaged), a Thesis Fit flag, and a rendered memo.
3. **This dashboard** (sidebar + pages) is the investor experience — a founder list, drill-down
   profile with score-history chart, memo download, and a natural-language query bar.

Use the sidebar to browse pages. The Thesis Engine on the sidebar edits your fund's thesis
config in real time: change geography or sectors and every founder's `thesis_fit` badge flips
across the app.

### Empty DB?

Click **Reload demo_data/*** in the sidebar, or from a terminal:
```bash
uv run python -m ventureos_ui.loader --reset demo_data
```
    """
)