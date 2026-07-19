"""CLI runner: invoke the LangGraph pipeline on one founder.

Usage:
    uv run python -m scripts.run_pipeline --name "Maya Chen" --company "Fetchly"
    uv run python -m scripts.run_pipeline --name "..." --company "..." --deck ./deck.txt --outbound
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ventureos.config import LOG_LEVEL
from ventureos.graph import build_graph
from ventureos.state import initial_state


def _json_default(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _load_thesis(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "sectors": ["dev tools", "AI infra"],
            "stage": "pre-seed",
            "geography": ["US", "EU"],
            "check_size": [25_000, 150_000],
            "ownership_target": 0.05,
            "risk_appetite": "high",
        }
    return json.loads(path.read_text())


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run the VentureOS pipeline on one founder.")
    parser.add_argument("--name", required=True, help="Founder name")
    parser.add_argument("--company", required=True, help="Company name")
    parser.add_argument("--deck", type=Path, default=None, help="Optional deck/application text file")
    parser.add_argument("--thesis", type=Path, default=None, help="Optional thesis_config JSON")
    parser.add_argument("--outbound", action="store_true", help="Flag this as an outbound-scan candidate")
    parser.add_argument("--out", type=Path, default=None, help="Optional path to write final state JSON")
    args = parser.parse_args()

    logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    application_text = args.deck.read_text() if args.deck and args.deck.exists() else ""
    thesis_config = _load_thesis(args.thesis)

    state = initial_state(
        founder_name=args.name,
        company=args.company,
        application_text=application_text,
        thesis_config=thesis_config,
        is_outbound=args.outbound,
    )

    graph = build_graph()
    final_state = await graph.ainvoke(state)

    output = json.dumps(final_state, default=_json_default, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))