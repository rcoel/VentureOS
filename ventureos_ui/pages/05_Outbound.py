"""Outbound Discovery — trigger a scan from the UI.

Runs `scripts/outbound_scan.discover_candidates` + the pipeline on N
candidates without leaving the browser. Streams progress live via
`st.status` so the user can watch each candidate go through.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from pydantic import BaseModel

from scripts.outbound_scan import (
    _discover_devpost,
    _discover_github_trending,
    _discover_show_hn,
    discover_candidates,
    run_pipeline_on_candidate,
)
from ventureos.graph import build_graph
from ventureos_ui.db import get_session
from ventureos_ui.loader import ensure_default_thesis, load_founder_json
from ventureos_ui.models_orm import ThesisConfig
from ventureos_ui.ui_helpers import (
    bootstrap,
    render_sidebar_data_ops,
    render_sidebar_thesis_editor,
    set_selected_founder,
)

st.set_page_config(page_title="Outbound · VentureOS", page_icon="🌐", layout="wide")
bootstrap()
render_sidebar_thesis_editor()
render_sidebar_data_ops()

st.title("Outbound Discovery")
st.caption(
    "Discover new founders from public feeds (Show HN + GitHub trending + Devpost "
    "hackathon winners) and run the full LangGraph pipeline on each. Results are "
    "loaded straight into the DB."
)


# --------------------------------------------------------------------------- #
# Controls                                                                    #
# --------------------------------------------------------------------------- #


with st.form("outbound_scan_form"):
    c1, c2, c3, c4 = st.columns(4)
    source = c1.selectbox(
        "Source",
        ["all (interleaved)", "hn_show only", "github_trending only", "devpost only"],
        index=0,
    )
    hours = c2.slider("Look-back window (hours)", min_value=24, max_value=720, value=168, step=24)
    per_source = c3.slider("Per-source cap", min_value=2, max_value=15, value=5)
    total_limit = c4.slider(
        "Total candidates to run (each ~30-60s)", min_value=1, max_value=15, value=3
    )
    submitted = st.form_submit_button("🚀 Run outbound scan", use_container_width=True)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _load_thesis_dict() -> dict[str, Any]:
    with get_session() as s:
        ensure_default_thesis(s)
        t = s.get(ThesisConfig, "current")
        if t is None:
            return {}
        return {
            "sectors": list(t.sectors or []),
            "stage": t.stage,
            "geography": list(t.geography or []),
            "check_size": [t.check_size_min, t.check_size_max],
            "ownership_target": t.ownership_target,
            "risk_appetite": t.risk_appetite,
        }


def _serialize(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj).__name__}")


async def _discover_only(source_key: str, hours: int, per_source: int) -> list[dict[str, Any]]:
    if source_key == "hn_show only":
        return await _discover_show_hn(hours, per_source)
    if source_key == "github_trending only":
        return await _discover_github_trending(hours, per_source)
    if source_key == "devpost only":
        return await _discover_devpost(limit_hackathons=per_source, limit_winners=per_source * 2)
    return await discover_candidates(hours, per_source, devpost_limit=per_source * 2)


# --------------------------------------------------------------------------- #
# Run                                                                         #
# --------------------------------------------------------------------------- #


if submitted:
    thesis = _load_thesis_dict()
    graph = build_graph()

    with st.status(f"Running outbound scan ({source}, limit={total_limit})...", expanded=True) as status:
        status.write("• Discovering candidates...")
        try:
            candidates = asyncio.run(_discover_only(source, hours, per_source))
        except Exception as e:
            status.update(label="Discovery failed", state="error")
            st.exception(e)
            st.stop()

        candidates = candidates[:total_limit]
        status.write(f"• Discovered {len(candidates)} candidate(s):")
        for i, c in enumerate(candidates, 1):
            status.write(f"    {i}. {c['founder_name']} / {c['company']} ({c['source']})")

        if not candidates:
            status.update(label="No candidates found", state="complete")
            st.info("The scan returned zero candidates. Try widening the look-back window.")
            st.stop()

        out_dir = Path("demo_data/outbound")
        out_dir.mkdir(parents=True, exist_ok=True)
        processed_ids: list[str] = []

        for i, cand in enumerate(candidates, 1):
            status.write(
                f"• [{i}/{len(candidates)}] Running pipeline: "
                f"{cand['founder_name']} / {cand['company']}..."
            )
            try:
                result = asyncio.run(run_pipeline_on_candidate(graph, cand, thesis))
            except Exception as e:
                status.write(f"    ⚠️ Pipeline failed: {e}")
                continue

            slug = "".join(ch if ch.isalnum() else "_" for ch in cand["company"].lower())[:40]
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            path = out_dir / f"{ts}_{cand['source']}_{slug}.json"
            path.write_text(json.dumps(result, default=_serialize, indent=2))
            status.write(f"    Wrote {path.name}")

            # Load straight into the DB
            with get_session() as s:
                fid = load_founder_json(path, session=s)
            processed_ids.append(fid)
            status.write(f"    Loaded founder_id={fid}")

        status.update(label=f"Done — processed {len(processed_ids)} candidate(s)", state="complete")

    if processed_ids:
        st.success(f"Discovered and loaded {len(processed_ids)} new founders into the dashboard.")
        st.page_link("pages/01_Dashboard.py", label="→ Open Dashboard", icon="📋")


# --------------------------------------------------------------------------- #
# Recent scan results                                                         #
# --------------------------------------------------------------------------- #

st.markdown("---")
st.markdown("### Recent outbound scan results")

out_dir = Path("demo_data/outbound")
if out_dir.exists():
    files = sorted(
        [p for p in out_dir.iterdir() if p.is_file() and not p.name.startswith("_")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:20]
    if files:
        rows = []
        for f in files:
            try:
                data = json.loads(f.read_text())
                fs = (data.get("final_state") or {})
                rows.append({
                    "File": f.name,
                    "Founder": fs.get("founder_name") or "—",
                    "Company": fs.get("company") or "—",
                    "Source": data.get("candidate_source") or "—",
                    "Screen": fs.get("screen_status") or "—",
                    "Score": fs.get("preliminary_score"),
                    "Modified": datetime.fromtimestamp(f.stat().st_mtime),
                })
            except Exception:
                continue
        if rows:
            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Modified": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
                    "Score": st.column_config.NumberColumn(format="%.1f"),
                },
            )
        else:
            st.caption("No parseable outbound scan results yet.")
    else:
        st.caption("No outbound scan results yet.")
else:
    st.caption("demo_data/outbound/ doesn't exist yet.")


# --------------------------------------------------------------------------- #
# Tips                                                                        #
# --------------------------------------------------------------------------- #

with st.expander("💡 How outbound discovery works"):
    st.markdown(
        """
- **Show HN** — pulls the latest Show HN posts from Hacker News Algolia.
  Each post's author becomes the founder, title becomes the company.
- **GitHub trending** — pulls recently-created repos with >10 stars,
  sorted by star count. Owner becomes the founder.
- **Devpost hackathon winners** — 3-step crawl:
  1. Tavily site-restricted search finds ended hackathon pages.
  2. Extracts each hackathon's project gallery.
  3. Pulls the winning project pages for team members, prizes, GitHub URLs.
- **Interleaved** — round-robin between all three, so a small total limit
  still gets a fair mix.

Each candidate then runs the same 8-node pipeline as an inbound application.
"""
    )