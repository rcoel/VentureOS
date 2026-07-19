"""Helpers used by every node: timing, reasoning log, error capture."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from ventureos.state import GraphState


@contextmanager
def node_trace(state: GraphState, node_name: str) -> Iterator[dict[str, Any]]:
    """Context manager: measures wall time, appends a trace entry on exit.

    Usage:
        with node_trace(state, "sourcing") as t:
            ... work ...
            t["evidence_count"] = len(evidence)
    """
    entry: dict[str, Any] = {"node": node_name}
    start = time.perf_counter()
    try:
        yield entry
    finally:
        entry["duration_ms"] = int((time.perf_counter() - start) * 1000)
        state.setdefault("trace", []).append(entry)


def log_reason(state: GraphState, node_name: str, reason: str) -> None:
    """Append a plain-English reasoning line for Agentic Traceability."""
    state.setdefault("reasoning_log", []).append({"node": node_name, "reason": reason})


def log_error(state: GraphState, node_name: str, error: str, **extra: Any) -> None:
    """Append a non-fatal error record. Never raises."""
    state.setdefault("errors", []).append({"node": node_name, "error": error, **extra})