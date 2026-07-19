"""Shared helpers for the Streamlit UI — session state, sidebar, formatters.

Kept UI-agnostic where possible so scoring/loader code can reuse the same
formatting logic (badges, colours) when writing memos.
"""

from __future__ import annotations

from typing import Any

import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ventureos_ui.db import get_session, init_db
from ventureos_ui.loader import ensure_default_thesis
from ventureos_ui.models_orm import Founder, ThesisConfig
from ventureos_ui.scoring.thesis_fit import recompute_all as recompute_all_thesis_fits


# --------------------------------------------------------------------------- #
# One-time bootstrap                                                          #
# --------------------------------------------------------------------------- #


def bootstrap() -> None:
    """Make sure the DB exists and the default thesis is present.

    Called at the top of every page. Idempotent.
    """
    if st.session_state.get("_bootstrapped"):
        return
    init_db()
    with get_session() as s:
        ensure_default_thesis(s)
    st.session_state["_bootstrapped"] = True


# --------------------------------------------------------------------------- #
# Selected-founder state (shared across pages)                                #
# --------------------------------------------------------------------------- #


def set_selected_founder(founder_id: str) -> None:
    st.session_state["selected_founder_id"] = founder_id


def get_selected_founder() -> str | None:
    return st.session_state.get("selected_founder_id")


# --------------------------------------------------------------------------- #
# Sidebar — persistent thesis config editor + navigation                      #
# --------------------------------------------------------------------------- #


_SECTOR_CHOICES = [
    "dev tools", "AI infra", "AI applications", "fintech", "healthtech",
    "biotech", "climate", "consumer", "prosumer", "developer platform",
    "security", "data infra", "edtech", "creator tools", "vertical SaaS",
]
_STAGE_CHOICES = ["pre-seed", "seed", "series A", "series B", "growth"]
_GEO_CHOICES = ["US", "EU", "UK", "APAC", "LATAM", "MENA", "Global"]
_RISK_CHOICES = ["low", "moderate", "high"]


def render_sidebar_thesis_editor() -> None:
    """Sidebar section: editable thesis config with live recompute.

    The form pattern: read current values → render widgets → on submit,
    write back + commit + recompute thesis_fit for ALL founders + rerun so
    the visible badges flip in real time.
    """
    # 1. Read current thesis in a short-lived session
    with get_session() as s:
        thesis = s.get(ThesisConfig, "current")
        assert thesis is not None, "ensure_default_thesis should have created it"
        current = {
            "sectors": list(thesis.sectors or []),
            "stage": thesis.stage,
            "geography": list(thesis.geography or []),
            "check_size_min": int(thesis.check_size_min or 0),
            "check_size_max": int(thesis.check_size_max or 0),
            "ownership_target": float(thesis.ownership_target or 0.0),
            "risk_appetite": thesis.risk_appetite,
        }

    # 2. Render the form (outside the session — Streamlit widgets don't need it)
    with st.sidebar:
        st.markdown("### 🎯 Thesis Engine")
        st.caption(
            f"Current: sectors=`{current['sectors']}` · stage=`{current['stage']}` "
            f"· geo=`{current['geography']}` · risk=`{current['risk_appetite']}`"
        )
        with st.form("thesis_form", clear_on_submit=False):
            sectors = st.multiselect(
                "Sectors",
                options=sorted(set(_SECTOR_CHOICES + current["sectors"])),
                default=current["sectors"],
                key="thesis_sectors",
            )
            stage = st.selectbox(
                "Stage",
                options=_STAGE_CHOICES,
                index=(
                    _STAGE_CHOICES.index(current["stage"])
                    if current["stage"] in _STAGE_CHOICES
                    else 0
                ),
                key="thesis_stage",
            )
            geography = st.multiselect(
                "Geography",
                options=_GEO_CHOICES,
                default=current["geography"],
                key="thesis_geo",
            )
            check_min, check_max = st.slider(
                "Check size (USD)",
                min_value=0,
                max_value=1_000_000,
                value=(current["check_size_min"], current["check_size_max"]),
                step=5_000,
                key="thesis_check",
            )
            ownership = st.slider(
                "Ownership target",
                min_value=0.0,
                max_value=0.25,
                value=current["ownership_target"],
                step=0.01,
                format="%.2f",
                key="thesis_ownership",
            )
            risk = st.selectbox(
                "Risk appetite",
                options=_RISK_CHOICES,
                index=(
                    _RISK_CHOICES.index(current["risk_appetite"])
                    if current["risk_appetite"] in _RISK_CHOICES
                    else 2
                ),
                key="thesis_risk",
            )
            submitted = st.form_submit_button("💾 Apply thesis", use_container_width=True)

    # 3. On submit, write back and recompute in a fresh session (safer than
    # keeping the earlier one open through the form render)
    if not submitted:
        return

    proposed = {
        "sectors": list(sectors or []),
        "stage": stage,
        "geography": list(geography or []),
        "check_size_min": int(check_min),
        "check_size_max": int(check_max),
        "ownership_target": float(ownership),
        "risk_appetite": risk,
    }

    if proposed == current:
        st.sidebar.info("No changes to apply.")
        return

    with get_session() as s:
        row = s.get(ThesisConfig, "current")
        row.sectors = proposed["sectors"]
        row.stage = proposed["stage"]
        row.geography = proposed["geography"]
        row.check_size_min = proposed["check_size_min"]
        row.check_size_max = proposed["check_size_max"]
        row.ownership_target = proposed["ownership_target"]
        row.risk_appetite = proposed["risk_appetite"]
        s.commit()

        # Recompute thesis fit for every founder — this is what flips the badges
        n = recompute_all_thesis_fits(s)

    # 4. Show what changed
    diffs = [k for k in proposed if proposed[k] != current[k]]
    st.sidebar.success(
        f"Thesis updated ({', '.join(diffs)}). Recomputed fit for {n} founder(s)."
    )
    st.rerun()


def render_sidebar_data_ops() -> None:
    """Sidebar section: reload demo data + row counts."""
    with st.sidebar:
        st.markdown("### 🗃️ Data")
        with get_session() as s:
            n_founders = s.scalar(select(func.count()).select_from(Founder)) or 0
        st.caption(f"{n_founders} founders in DB")
        if st.button("Reload demo_data/*"):
            from pathlib import Path
            from ventureos_ui.loader import load_dir
            with get_session() as s:
                inbound = load_dir(Path("demo_data"), session=s)
            st.success(f"Reloaded {len(inbound)} files.")
            st.rerun()


# --------------------------------------------------------------------------- #
# Formatting helpers                                                          #
# --------------------------------------------------------------------------- #


def score_color(score: float | None) -> str:
    if score is None:
        return "gray"
    if score >= 70:
        return "green"
    if score >= 50:
        return "orange"
    return "red"


def thesis_badge_md(thesis_fit: str) -> str:
    if thesis_fit == "in_thesis":
        return "🟢 `in_thesis`"
    if thesis_fit == "outside_thesis":
        return "🔴 `outside_thesis`"
    return "⚪ `unknown`"


def screen_badge_md(status: str) -> str:
    return {"PASS": "✅ PASS", "FAIL": "❌ FAIL"}.get(status, "⏳ pending")


def not_disclosed(v: Any) -> str:
    if v is None or v == "" or v == []:
        return "[Not Disclosed]"
    return str(v)