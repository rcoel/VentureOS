"""Inbound Application — deck + founder/company → run pipeline → load to DB.

This is MVP requirement #4: "Apply — deck + company name is the minimum bar."

Flow:
1. User enters founder_name + company (required).
2. User uploads a PDF/TXT/MD deck OR pastes text.
3. Optionally toggles "outbound" (rare here — this page is for inbound apps).
4. Click Submit.
5. Pipeline runs synchronously via `asyncio.run(graph.ainvoke(...))`.
6. Result is loaded into the DB and the user is redirected to the profile.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from ventureos_ui.db import get_session
from ventureos_ui.loader import _extract_final_state, load_founder_json, make_founder_id
from ventureos_ui.models_orm import ThesisConfig
from ventureos_ui.ui_helpers import (
    bootstrap,
    render_sidebar_data_ops,
    render_sidebar_thesis_editor,
    set_selected_founder,
)

log = logging.getLogger("ventureos_ui.apply")

st.set_page_config(page_title="Apply · VentureOS", page_icon="📥", layout="wide")
bootstrap()
render_sidebar_thesis_editor()
render_sidebar_data_ops()

st.title("Founder Application")
st.caption(
    "Submit a founder + slide deck. The pipeline runs live "
    "(intake → screening → sourcing → extraction → verification → attributes "
    "→ market research → activation) and the result loads directly into the DB."
)

# --------------------------------------------------------------------------- #
# PDF text extraction                                                         #
# --------------------------------------------------------------------------- #


def _extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages_text = []
    for i, page in enumerate(reader.pages):
        try:
            pages_text.append(page.extract_text() or "")
        except Exception as e:
            log.warning("Failed to extract PDF page %d: %s", i, e)
    return "\n\n".join(pages_text).strip()


def _extract_deck_text(uploaded_file) -> str:
    name = uploaded_file.name.lower()
    data = uploaded_file.getvalue()
    if name.endswith(".pdf"):
        return _extract_pdf_text(data)
    # Assume text-like format
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


# --------------------------------------------------------------------------- #
# Form                                                                        #
# --------------------------------------------------------------------------- #


with st.form("apply_form", clear_on_submit=False):
    col1, col2 = st.columns(2)
    with col1:
        founder_name = st.text_input(
            "Founder name *", value="", placeholder="e.g. Maya Chen"
        )
    with col2:
        company = st.text_input(
            "Company / product name *", value="", placeholder="e.g. Fetchly"
        )

    st.markdown("#### Deck / application text")
    uploaded = st.file_uploader(
        "Upload deck (PDF, TXT, or Markdown)",
        type=["pdf", "txt", "md"],
        accept_multiple_files=False,
    )
    pasted_text = st.text_area(
        "…or paste application text",
        value="",
        height=200,
        placeholder=(
            "Paste your pitch here. What is the product? Who is it for? "
            "What traction? Who is on the team? What are you raising?"
        ),
    )

    outbound = st.checkbox(
        "Flag as outbound (only tick if this candidate came from a discovery scan)",
        value=False,
    )

    submitted = st.form_submit_button("🚀 Submit application")

# --------------------------------------------------------------------------- #
# Pipeline invocation                                                         #
# --------------------------------------------------------------------------- #


def _load_thesis_dict() -> dict[str, Any]:
    """Read the current ThesisConfig from DB and shape it for the pipeline."""
    with get_session() as s:
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


async def _run_pipeline(
    founder_name: str, company: str, deck_text: str, thesis: dict[str, Any], is_outbound: bool
) -> dict[str, Any]:
    """Invoke the LangGraph pipeline and return the final state dict."""
    from ventureos.graph import build_graph
    from ventureos.state import initial_state

    graph = build_graph()
    state = initial_state(
        founder_name=founder_name,
        company=company,
        application_text=deck_text,
        thesis_config=thesis,
        is_outbound=is_outbound,
    )
    final = await graph.ainvoke(state)
    return final


def _serialize(obj: Any) -> Any:
    from pydantic import BaseModel

    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj).__name__}")


if submitted:
    # === Validate ===
    errors = []
    if not founder_name.strip():
        errors.append("Founder name is required.")
    if not company.strip():
        errors.append("Company name is required.")

    deck_text = ""
    if uploaded is not None:
        try:
            deck_text = _extract_deck_text(uploaded)
        except Exception as e:
            errors.append(f"Failed to extract text from upload: {e}")

    # Merge pasted text at the end (if both provided, join them)
    if pasted_text.strip():
        deck_text = f"{deck_text}\n\n{pasted_text.strip()}".strip() if deck_text else pasted_text.strip()

    if not deck_text or len(deck_text) < 40:
        errors.append(
            "Deck / application text is too short — please upload a deck or paste at least a paragraph."
        )

    if errors:
        for e in errors:
            st.error(e)
        st.stop()

    st.info(f"Application text: {len(deck_text)} characters. Running pipeline…")
    thesis = _load_thesis_dict()

    with st.status("Running pipeline…", expanded=True) as status:
        status.write("• Building LangGraph")
        try:
            final_state = asyncio.run(
                _run_pipeline(founder_name, company, deck_text, thesis, outbound)
            )
        except Exception as e:
            status.update(label="Pipeline failed", state="error")
            st.exception(e)
            st.stop()

        status.write("• Pipeline complete — writing to DB")

        # Persist a JSON file so it lives under demo_data/inbound (audit trail)
        out_dir = Path("demo_data/inbound")
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = "".join(c if c.isalnum() else "_" for c in company.lower())[:40]
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"{ts}_{slug}.json"
        out_path.write_text(json.dumps(final_state, default=_serialize, indent=2))
        status.write(f"• Wrote {out_path}")

        # Load into DB (recomputes score + axes + thesis + memo)
        with get_session() as s:
            fid = load_founder_json(out_path, session=s)
        status.write(f"• Loaded founder_id = `{fid}`")
        status.update(label="Done — application processed", state="complete")

    set_selected_founder(fid)
    st.success(f"Application processed for **{company}**.")

    # Show the agent trail inline — this is the "watch the pipeline think" moment
    from ventureos_ui.agent_trace_view import render_agent_trace_compact
    render_agent_trace_compact(final_state.get("reasoning_log"))

    st.page_link("pages/02_Founder_Profile.py", label="→ Open Founder Profile", icon="👤")
    st.page_link("pages/03_Memo.py", label="→ Read Investment Memo", icon="📄")

# --------------------------------------------------------------------------- #
# Recent inbound applications                                                 #
# --------------------------------------------------------------------------- #

with st.expander("Recent inbound applications", expanded=False):
    inbound_dir = Path("demo_data/inbound")
    if inbound_dir.exists():
        files = sorted(inbound_dir.glob("*.json"), reverse=True)[:10]
        if files:
            for f in files:
                st.caption(f"{f.name}  ·  {f.stat().st_size // 1024} KB")
        else:
            st.caption("No inbound applications yet.")
    else:
        st.caption("No inbound applications yet.")