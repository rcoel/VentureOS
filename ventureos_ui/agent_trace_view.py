"""Streamlit renderer for the agent trace (reasoning_log + trace + errors).

Kept as a shared component because it's used from both the Founder Profile
page and the Apply page (right after a pipeline run).
"""

from __future__ import annotations

from typing import Any

import streamlit as st

# Emoji + short label per node so the timeline reads at a glance.
_NODE_STYLE: dict[str, tuple[str, str]] = {
    "intake": ("📥", "Intake"),
    "screening": ("🚦", "Screening"),
    "sourcing": ("🔎", "Sourcing"),
    "extraction": ("🧬", "Extraction"),
    "verification": ("✅", "Verification"),
    "attributes_rollup": ("🧮", "Attributes rollup"),
    "market_research": ("📊", "Market research"),
    "activation": ("✉️", "Activation"),
}


def _fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms} ms"
    return f"{ms / 1000:.2f} s"


def _errors_for_node(errors: list[dict[str, Any]], node: str) -> list[dict[str, Any]]:
    return [e for e in errors if (e or {}).get("node") == node]


def render_agent_trace(
    reasoning_log: list[dict[str, Any]] | None,
    trace: list[dict[str, Any]] | None,
    errors: list[dict[str, Any]] | None,
) -> None:
    """Render the full agent trace as a timeline + expanders.

    Call inside a Streamlit page. Safe when inputs are None / empty.
    """
    reasoning_log = reasoning_log or []
    trace = trace or []
    errors = errors or []

    if not reasoning_log and not trace:
        st.info(
            "No agent trace recorded for this founder. Trace is captured "
            "automatically on new pipeline runs."
        )
        return

    # Map node → trace entry (some pipelines may not emit trace but do emit
    # reasoning, or vice versa)
    trace_by_node: dict[str, dict[str, Any]] = {}
    for t in trace:
        node = t.get("node")
        if node and node not in trace_by_node:
            trace_by_node[node] = t

    reasoning_by_node: dict[str, str] = {
        (r.get("node") or "?"): (r.get("reason") or "") for r in reasoning_log
    }

    # Union of node keys, in the order they first appear in the trace
    ordered_nodes: list[str] = []
    seen: set[str] = set()
    for r in reasoning_log + trace:
        n = r.get("node")
        if n and n not in seen:
            seen.add(n)
            ordered_nodes.append(n)

    # Top summary line
    total_ms = sum((t.get("duration_ms") or 0) for t in trace)
    st.caption(
        f"**{len(ordered_nodes)} nodes** · total pipeline wall-clock "
        f"**{_fmt_duration(total_ms)}** · **{len(errors)}** non-fatal error(s)"
    )

    # Errors block first (if any) — investors want to see them upfront.
    if errors:
        with st.expander(f"⚠️ Errors ({len(errors)})", expanded=False):
            for e in errors:
                st.markdown(
                    f"- **[{e.get('node', '?')}]** {e.get('error', '')}"
                    + (f" · `{e.get('tool')}`" if e.get("tool") else "")
                )

    # Node-by-node timeline
    for i, node in enumerate(ordered_nodes, 1):
        emoji, label = _NODE_STYLE.get(node, ("•", node))
        t = trace_by_node.get(node, {})
        duration = _fmt_duration(t.get("duration_ms"))
        reasoning = reasoning_by_node.get(node, "").strip()
        node_errors = _errors_for_node(errors, node)

        header = f"{emoji} **{i}. {label}** · `{duration}`"
        if node_errors:
            header += f" · ⚠️ {len(node_errors)} error(s)"

        with st.expander(header, expanded=i <= 3):
            if reasoning:
                st.markdown(f"> {reasoning}")
            else:
                st.caption("_no reasoning logged for this node_")

            # Node-specific metrics from the trace payload
            metrics = {k: v for k, v in t.items() if k not in ("node", "duration_ms")}
            if metrics:
                cols = st.columns(min(4, len(metrics)) or 1)
                for j, (k, v) in enumerate(metrics.items()):
                    with cols[j % len(cols)]:
                        display = v
                        if isinstance(v, (dict, list)):
                            display = str(v)[:80]
                        st.metric(k.replace("_", " "), display)

            if node_errors:
                st.markdown("**Errors from this node:**")
                for e in node_errors:
                    st.markdown(f"- {e.get('error', '')}" + (f" · `{e.get('tool')}`" if e.get("tool") else ""))

    with st.expander("🔬 Raw JSON (debug)", expanded=False):
        st.json({"reasoning_log": reasoning_log, "trace": trace, "errors": errors})


def render_agent_trace_compact(reasoning_log: list[dict[str, Any]] | None) -> None:
    """One-line-per-node compact view. Used inline on the Apply page after
    a fresh pipeline run so the user sees the agent trail immediately."""
    reasoning_log = reasoning_log or []
    if not reasoning_log:
        return
    st.markdown("#### 🧠 Agent trail (this pipeline run)")
    for r in reasoning_log:
        node = r.get("node", "?")
        emoji, label = _NODE_STYLE.get(node, ("•", node))
        reason = r.get("reason", "")
        st.markdown(f"- {emoji} **{label}** — {reason}")