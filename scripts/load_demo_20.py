"""Bulk load 20 founders — 15 outbound (Devpost + HN + GitHub) + 5 curated.

Fresh-run behavior:
  * Wipes the UI DB (drops all tables and recreates them).
  * Deletes all files under demo_data/outbound and demo_data/inbound.
  * Clears LLM cache entries (`llm:*`) — tool cache preserved.
  * Runs the outbound scan for 15 candidates.
  * Runs the pipeline on 5 curated well-known founders with hand-written deck text.
  * Loads all 20 JSONs into the DB.

Usage:
    uv run python -m scripts.load_demo_20 --yes

Approximate cost / time (with tool cache preserved):
  ~$0.30–0.60 in OpenAI + Tavily + SerpAPI
  ~20–40 minutes wall-clock
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel

from ventureos.config import CACHE_PATH
from ventureos.graph import build_graph
from ventureos.state import initial_state
from ventureos_ui.db import get_session, get_engine, init_db
from ventureos_ui.loader import ensure_default_thesis, load_founder_json, load_dir
from ventureos_ui.models_orm import Base

log = logging.getLogger("ventureos.load_demo_20")


# --------------------------------------------------------------------------- #
# Curated 5 well-known founders                                                #
# --------------------------------------------------------------------------- #

CURATED: list[dict[str, Any]] = [
    {
        "founder_name": "Nat Friedman",
        "company": "AI Grant",
        "application_text": (
            "AI Grant is an accelerator focused on funding early-stage AI startups.\n\n"
            "Founder Nat Friedman is the former CEO of GitHub (2018-2021), where he led the "
            "company through Microsoft's acquisition and later oversaw GitHub Copilot. Before that, "
            "he co-founded Xamarin (acquired by Microsoft) and Ximian.\n\n"
            "Together with Daniel Gross, Nat runs AI Grant, providing $250K grants and compute to "
            "AI founders. Prior exits: 2. Well-known angel investor across the AI ecosystem.\n\n"
            "Category: accelerator / AI infra.\n"
            "Location: San Francisco."
        ),
        "is_outbound": False,
    },
    {
        "founder_name": "Andrej Karpathy",
        "company": "Eureka Labs",
        "application_text": (
            "Eureka Labs is a new AI-native school. We're building the teacher-student loop for "
            "the AI age, starting with a course on training large language models from scratch.\n\n"
            "Founder Andrej Karpathy has a PhD from Stanford in computer vision and deep learning "
            "under Fei-Fei Li. He was a founding member and research scientist at OpenAI, then led "
            "AI at Tesla (Autopilot, Tesla Vision) from 2017 to 2022. He returned to OpenAI in 2023 "
            "and left in 2024 to start Eureka. Author of the widely-used 'nanoGPT' repo and "
            "the 'Neural Networks: Zero to Hero' YouTube series.\n\n"
            "Category: AI applications, edtech, AI-native tools.\n"
            "Location: San Francisco. Highly technical solo founder to start; hiring."
        ),
        "is_outbound": False,
    },
]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _serialize(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj).__name__}")


async def _clear_llm_cache() -> int:
    """Delete only LLM cache entries; keep tool responses."""
    if not CACHE_PATH.exists():
        return 0
    async with aiosqlite.connect(str(CACHE_PATH)) as db:
        cur = await db.execute("DELETE FROM cache WHERE key LIKE 'llm:%'")
        await db.commit()
        return cur.rowcount


def _reset_db() -> None:
    engine = get_engine()
    Base.metadata.drop_all(engine)
    init_db()
    with get_session() as s:
        ensure_default_thesis(s)


def _clear_demo_data() -> None:
    for sub in ("outbound", "inbound"):
        d = Path("demo_data") / sub
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()


async def _run_curated() -> list[Path]:
    """Run the pipeline on each curated founder and write JSON files."""
    from scripts.run_pipeline import _load_thesis, _json_default  # reuse defaults

    graph = build_graph()
    thesis = _load_thesis(None)

    out_dir = Path("demo_data/inbound")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for i, entry in enumerate(CURATED, 1):
        log.info(
            "Curated [%d/%d] %s / %s",
            i, len(CURATED), entry["founder_name"], entry["company"],
        )
        state = initial_state(
            founder_name=entry["founder_name"],
            company=entry["company"],
            application_text=entry["application_text"],
            thesis_config=thesis,
            is_outbound=entry.get("is_outbound", False),
        )
        try:
            final = await graph.ainvoke(state)
        except Exception as e:
            log.warning("Curated pipeline failed for %s: %s", entry["company"], e)
            continue
        slug = "".join(c if c.isalnum() else "_" for c in entry["company"].lower())[:40]
        p = out_dir / f"curated_{i:02d}_{slug}.json"
        p.write_text(json.dumps(final, default=_serialize, indent=2))
        paths.append(p)
        log.info("  wrote %s", p)
    return paths


def _run_outbound_scan(limit: int, per_source: int, devpost_limit: int, hours: int) -> None:
    """Invoke the outbound scan as if it were called from the CLI."""
    import sys
    from scripts.outbound_scan import main as outbound_main

    argv_backup = sys.argv[:]
    sys.argv = [
        "outbound_scan",
        "--hours", str(hours),
        "--per-source", str(per_source),
        "--devpost-limit", str(devpost_limit),
        "--limit", str(limit),
    ]
    try:
        asyncio.run(outbound_main())
    finally:
        sys.argv = argv_backup


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset + load N founders: (N-2) outbound + 2 curated (Nat Friedman + Andrej Karpathy)."
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument("--outbound-limit", type=int, default=8, help="Outbound candidates to run (default 8, giving 8+2=10 total)")
    parser.add_argument("--per-source", type=int, default=5, help="Max per source (HN/GitHub/Devpost) before interleaving")
    parser.add_argument("--devpost-limit", type=int, default=5)
    parser.add_argument("--hours", type=int, default=720)
    parser.add_argument("--skip-outbound", action="store_true", help="Only run curated.")
    parser.add_argument("--skip-curated", action="store_true", help="Only run outbound.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    if not args.yes:
        confirm = input(
            "This will DROP the UI DB, clear demo_data/inbound and demo_data/outbound, "
            "and clear LLM cache. Continue? [y/N] "
        )
        if confirm.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    log.info("Step 1/4 — resetting UI DB")
    _reset_db()

    log.info("Step 2/4 — clearing demo_data/outbound and demo_data/inbound")
    _clear_demo_data()

    log.info("Step 3/4 — clearing LLM cache (keeping tool cache)")
    cleared = asyncio.run(_clear_llm_cache())
    log.info("  cleared %d LLM cache entries", cleared)

    log.info("Step 4/4 — running pipelines")

    if not args.skip_outbound:
        log.info(
            "→ Outbound scan (limit=%d, per_source=%d, devpost_limit=%d, hours=%d)",
            args.outbound_limit, args.per_source, args.devpost_limit, args.hours,
        )
        _run_outbound_scan(
            args.outbound_limit, args.per_source, args.devpost_limit, args.hours
        )
    else:
        log.info("→ Skipping outbound scan")

    if not args.skip_curated:
        log.info("→ Running curated 5 founders")
        asyncio.run(_run_curated())
    else:
        log.info("→ Skipping curated")

    log.info("Step 5/5 — loading all JSONs into DB")
    with get_session() as s:
        ensure_default_thesis(s)
        ids = load_dir(Path("demo_data"), session=s)
    log.info("Loaded %d founders total.", len(ids))

    # Summary
    from sqlalchemy import select
    from ventureos_ui.models_orm import Founder, FounderScore, ThesisFit

    log.info("=" * 90)
    log.info("SUMMARY")
    log.info("=" * 90)
    with get_session() as s:
        rows = s.execute(
            select(Founder, FounderScore, ThesisFit)
            .join(FounderScore, FounderScore.founder_id == Founder.id, isouter=True)
            .join(ThesisFit, ThesisFit.founder_id == Founder.id, isouter=True)
            .order_by(FounderScore.founder_score.desc().nullslast())
        ).all()
        for founder, fs, tf in rows:
            score = f"{fs.founder_score:5.1f}" if fs else "  —  "
            cold = "❄️" if fs and fs.cold_start_applied else "  "
            thesis = tf.thesis_fit if tf else "-"
            log.info(
                "  %-25s %-25s %s  %s  %-15s  %s",
                founder.company[:24], founder.founder_name[:24], score, cold, thesis, founder.source,
            )
    log.info("=" * 90)
    log.info("Done. Launch the app with: uv run streamlit run ventureos_ui/app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())