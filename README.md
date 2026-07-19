# VentureOS

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/rcoel/VentureOS)

> An evidence-backed VC screening system. Applies a founder → sources
> real-world signal from 6 different data sources → extracts, verifies,
> and scores → produces a cited investment memo. Live pipeline traces,
> a natural-language query bar, and a real Streamlit dashboard, not a slide.

---

## Table of contents

1. [What this actually does](#what-this-actually-does)
2. [The architecture in one picture](#the-architecture-in-one-picture)
3. [Quick start](#quick-start)
4. [Directory layout](#directory-layout)
5. [The pipeline in detail](#the-pipeline-in-detail)
6. [The scoring engine](#the-scoring-engine)
7. [The Streamlit app](#the-streamlit-app)
8. [Data flow: JSON → SQLite → UI](#data-flow-json--sqlite--ui)
9. [Deployment](#deployment)
10. [Cost and rate limits](#cost-and-rate-limits)
11. [Extending the pipeline](#extending-the-pipeline)
12. [Troubleshooting](#troubleshooting)

---

## What this actually does

Given a founder's name + company + a pitch deck (or nothing at all, in
outbound mode), VentureOS runs an **8-node LangGraph pipeline** that:

1. **Reads the deck** and extracts hints (categories, GitHub URLs, location).
2. **Screens** the application against a fund thesis using an LLM. Fails
   fast on vague or non-startup pitches.
3. **Sources evidence** in parallel from:
   - GitHub REST API — user profile, top repos, commit activity
   - Hacker News Algolia — Show HN posts, story mentions
   - Semantic Scholar — papers + h-index (research founders)
   - Tavily Search + Extract — general web narrative + Devpost pages
   - SerpAPI Google — site-restricted searches (Product Hunt, YC,
     LinkedIn) and **Google AI Mode** for cited market research
4. **Extracts structured claims** from every evidence blob with a
   Pydantic-typed LLM call, each carrying a `source_evidence_id`.
5. **Verifies** claims across sources — flags contradictions (e.g., deck
   says $50K MRR, no press coverage). Fires a Tavily fallback search on
   high-stakes solo claims like funding numbers.
6. **Rolls attributes up** into typed fields (`is_technical`, `location`,
   `is_researcher`, `h_index`, etc.) — unknowns stay `null`.
7. **Runs market research** — a **Google AI Mode** synthesis (with 15-20
   citations), plus SerpAPI + Tavily supplemental queries, plus a
   Semantic Scholar domain search. All 9 queries fan out in parallel.
   Produces both a `MarketResearch` (stance, competitors, TAM) and a
   **citation-backed SWOTAnalysis** (every bullet points at a real URL).
8. **Activates** — for outbound candidates with a preliminary score ≥ 60,
   drafts a 150-word cold outreach email.

Every LLM call runs against a Pydantic schema so outputs are typed data,
not JSON strings. Every tool call is cached in SQLite so re-runs are fast
and cheap. Every node writes a one-line reasoning entry — the "why" is
visible in the UI and in the downloadable memo.

Then a **SQLAlchemy 2.0 + Streamlit** layer consumes those pipeline JSONs
and turns them into an investor experience: dashboard, drill-down profile
with a Plotly score-history chart, downloadable Markdown memo, natural-
language query bar, and a Thesis Engine sidebar that flips fit badges live.

---

## The architecture in one picture

```
                        ┌────────────────────────────────────────────┐
                        │             STREAMLIT DASHBOARD            │
                        │   ⬢ Dashboard   ⬢ Apply    ⬢ Query        │
                        │   ⬢ Founder Profile (score card,          │
                        │      3-axis panel, Plotly score history,  │
                        │      Agent Trace, SWOT with citations)    │
                        │   ⬢ Investment Memo (Markdown + download) │
                        └───────────────────┬────────────────────────┘
                                            │ reads
┌───────────────────────────────────────────▼──────────────────────────────┐
│                          UI DB (SQLite via SQLAlchemy 2.0)                │
│  founder │ evidence_item │ claim │ contradiction │ market_research │      │
│  swot_entry │ founder_score │ axis_score │ score_history │ thesis_config │
│  thesis_fit │ memo                                                        │
└───────────────────▲───────────────────────────────────────────────────────┘
                    │ upserts via ventureos_ui.loader
┌───────────────────┴───────────────────────────────────────────────────────┐
│                       LANGGRAPH PIPELINE (ventureos/graph.py)             │
│  ┌──────┐  ┌────────┐  ┌─────────┐  ┌───────────┐  ┌───────────┐         │
│  │Intake│→ │Screen  │→ │Sourcing │→ │Extraction │→ │Verification│─┐       │
│  └──────┘  └────────┘  └─────────┘  └───────────┘  └───────────┘  │       │
│                                                                    ▼       │
│  ┌─────────────┐   ┌──────────────────┐    ┌────────────┐                 │
│  │ Attributes  │ ← │   Market         │ ←  │ Activation │  → final_state  │
│  │ rollup      │   │   Research +     │    │ (outreach) │                 │
│  └─────────────┘   │   SWOT synth     │    └────────────┘                 │
│                    │ (Google AI Mode +│                                    │
│                    │  Tavily + S2)    │                                    │
│                    └──────────────────┘                                    │
└──────────────┬────────────────────────────────────────────────────────────┘
               │ writes evidence + claims to
               ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                  TOOL CACHE (SQLite via aiosqlite)                         │
│    keyed by hash(query + tool). LLM cache + tool response cache.          │
│    Keeps repeat runs free.                                                 │
└──────────────┬────────────────────────────────────────────────────────────┘
               │ hits when tools would otherwise call
               ▼
     ┌─────────┴─────────────────────────────────────────────┐
     │  GitHub REST │ HN Algolia │ Semantic Scholar │ Tavily │
     │  Search+Extract           │ SerpAPI (Google + AI Mode)│
     └────────────────────────────────────────────────────────┘
```

Data flows **up only**: sourcing writes evidence; intelligence reads
evidence and writes claims/scores; experience only reads. This one-way
rule prevents state-drift bugs under load.

---

## Quick start

### Prerequisites

- **Python 3.11+**
- [**uv**](https://github.com/astral-sh/uv) (fast Python package manager)
- API keys for: `OPENAI_API_KEY`, `TAVILY_API_KEY`, `SERPAPI_API_KEY`,
  `GITHUB_TOKEN`. `SEMANTIC_SCHOLAR_API_KEY` is optional but recommended.

### Setup

```bash
# 1. Clone and install
git clone https://github.com/rcoel/VentureOS.git
cd VentureOS
uv sync

# 2. Configure API keys
cp .env.example .env
$EDITOR .env

# 3. Load demo data (creates DB + runs 7 founders through the pipeline)
uv run python -m scripts.load_demo_20 --yes

# 4. Launch the dashboard
uv run streamlit run ventureos_ui/app.py
```

Open [http://localhost:8501](http://localhost:8501). Should show 7-9
founders in the dashboard.

### Applying a new founder

Either **from the Apply page** (upload PDF/TXT deck through the browser)
or the CLI:

```bash
uv run python -m scripts.run_pipeline \
    --name "Maya Chen" \
    --company "Fetchly" \
    --deck ./deck.txt \
    --out demo_data/inbound/fetchly.json

# Then load into the UI
uv run python -m ventureos_ui.loader demo_data/inbound/fetchly.json
```

### Outbound discovery scan

```bash
uv run python -m scripts.outbound_scan \
    --limit 10 --per-source 5 --devpost-limit 5 --hours 168
```

Pulls candidates from Show HN + GitHub trending + Devpost hackathon
winners, round-robin interleaved for a fair mix, and runs the full
pipeline on each.

---

## Directory layout

```
VentureOS/
├── ventureos/                    ← LangGraph pipeline
│   ├── graph.py                    Wire the 8 nodes together
│   ├── state.py                    Typed pipeline state
│   ├── models.py                   Frozen Pydantic contract (shared with UI)
│   ├── config.py                   Env vars + cache path
│   ├── cache.py                    Tool + LLM cache (SQLite via aiosqlite)
│   ├── llm.py                      OpenAI structured-output helper
│   ├── nodes/                      One .py per LangGraph node
│   │   ├── intake.py
│   │   ├── screening.py
│   │   ├── sourcing.py
│   │   ├── extraction.py
│   │   ├── verification.py
│   │   ├── attributes_rollup.py
│   │   ├── market_research.py    ← Google AI Mode + SWOT synthesis lives here
│   │   └── activation.py
│   ├── prompts/                    All LLM prompts (Markdown, hot-swappable)
│   └── tools/
│       ├── github.py               GitHub REST
│       ├── github_discovery.py     SerpAPI + GitHub-API verification
│       ├── hn.py                   HN Algolia
│       ├── semantic_scholar.py     Papers + h-index + domain search
│       ├── tavily_tool.py          Tavily Search + Extract
│       └── serpapi_tool.py         SerpAPI Google + Google AI Mode
│
├── ventureos_ui/                 ← DB + scoring + Streamlit
│   ├── app.py                      Main page (home + metrics)
│   ├── db.py                       SQLAlchemy engine + session factory
│   ├── models_orm.py               13 ORM tables mirroring the Pydantic contract
│   ├── loader.py                   demo_data/**/*.json → DB (idempotent)
│   ├── ui_helpers.py               Shared sidebar + formatters
│   ├── agent_trace_view.py         Renders reasoning_log + trace + errors
│   ├── memo/
│   │   ├── memo_builder.py         5-section Markdown memo assembler
│   │   ├── swot.py                 Citation-aware SWOT view
│   │   └── query_parser.py         NL query → QueryFilter → SQL
│   ├── scoring/
│   │   ├── constants.py            Weights, decay, source-trust table
│   │   ├── trust_score.py          Per-claim trust = base × verification
│   │   ├── founder_score.py        4-tier + cold-start reweighting
│   │   ├── axis_scores.py          3-axis (Founder / Market / Idea-vs-Market)
│   │   ├── thesis_fit.py           Thesis Engine
│   │   └── trends.py               improving / declining / stable
│   └── pages/                      Streamlit multipage app
│       ├── 00_Apply.py             Deck upload + live pipeline run
│       ├── 01_Dashboard.py         Founder list
│       ├── 02_Founder_Profile.py   Score card + 3-axis + history + tabs
│       ├── 03_Memo.py              Markdown render + download
│       └── 04_Query.py             Natural-language query bar
│
├── scripts/
│   ├── run_pipeline.py             Single-founder CLI
│   ├── outbound_scan.py            Show HN + GitHub trending + Devpost discovery
│   ├── load_demo_20.py             Full reset + bulk load
│   └── render_start.sh             Render.com startup entrypoint
│
├── demo_data/
│   ├── inbound/                    Curated + Apply-page submissions
│   └── outbound/                   Outbound-scan results + _summary.json
│
├── render.yaml                     Render.com Blueprint
├── DEPLOY_RENDER.md                Render.com deployment walkthrough
├── pyproject.toml                  uv + hatchling build config
└── .env.example                    Required env vars
```

---

## The pipeline in detail

### Node-by-node

| Node | Reads | Writes | LLM? | Prompt |
|---|---|---|---|---|
| **intake** | `application_text`, `founder_name`, `company` | `IntakeSummary` (github handle hints, category labels, product URLs, location, is_research) | `gpt-4o-mini` | [`intake.md`](./ventureos/prompts/intake.md) |
| **screening** | intake + application_text | `screen_status: PASS \| FAIL`, `screen_reason` | `gpt-4o-mini` | [`screening.md`](./ventureos/prompts/screening.md) |
| **sourcing** | intake | `raw_evidence: list[EvidenceItem]` — parallel fan-out to GitHub, HN, S2, Tavily, SerpAPI | — | — |
| **extraction** | evidence items | `claims: list[Claim]` — one call per evidence blob, source-specific prompt | `gpt-4o-mini` × N | 6 source-specific prompts under [`prompts/extraction_*.md`](./ventureos/prompts/) |
| **verification** | claims grouped by predicate | `contradictions[]`, `verification_map: {claim_id → verified\|unverifiable\|contradicted}` | `gpt-4o` | [`verification.md`](./ventureos/prompts/verification.md) |
| **attributes_rollup** | verified claims + intake | `FounderAttributes` (typed rollup) | `gpt-4o-mini` | [`attributes_rollup.md`](./ventureos/prompts/attributes_rollup.md) |
| **market_research** | attributes.categories | `MarketResearch` + `SWOTAnalysis` (both citation-backed via Google AI Mode) | `gpt-4o` × 2 | [`market_research.md`](./ventureos/prompts/market_research.md) + [`swot_synthesis.md`](./ventureos/prompts/swot_synthesis.md) |
| **activation** | full state, if `is_outbound=True` and score ≥ 60 | `outreach_draft` (150-word cold email) | `gpt-4o` | [`activation.md`](./ventureos/prompts/activation.md) |

### Key design invariants

- **Empty results are evidence.** If we search GitHub and find nothing,
  we write a `github_absence` claim, not a `github` claim with confidence
  0. Cold-start founders shouldn't be silently punished for lacking a
  network.
- **Every claim carries `source_evidence_id`.** This is the traceability
  spine. Memo rendering, contradiction detection, and trust scoring all
  join through it.
- **LLM outputs are Pydantic-typed.** We use OpenAI's structured
  outputs feature — the model literally can't return non-schema JSON,
  so we never have "just JSON please" prompting hacks.
- **Identity guardrails everywhere.** The GitHub extractor won't
  attribute claims to a profile unless it matches the founder name
  or company. `github_discovery.py` verifies handles via the GitHub API
  before we probe repos.
- **No handle-guessing bloat.** SerpAPI Google + strict GitHub-API
  verification finds handles like `rauchg` (for Guillermo Rauch) that no
  `firstlast` guesser would produce.

---

## The scoring engine

### 4-tier Founder Score with cold-start reweighting

```
FounderScore = w₁·TrackRecord + w₂·ExecutionSignal +
               w₃·NarrativeQuality + w₄·Consistency
```

Base weights: `w₁=0.35, w₂=0.30, w₃=0.20, w₄=0.15`.

**Cold-start rule** (`scoring/founder_score.py`): if TrackRecord and/or
ExecutionSignal have no supporting evidence (not zero score — actually
zero evidence), that weight is *redistributed* into NarrativeQuality and
Consistency rather than counted as zero. A cold-start founder gets
`weights_used = {track_record: None, execution_signal: None,
narrative_quality: 0.65, consistency: 0.35}` and the UI + memo make it
explicit that reweighting fired.

### Per-claim trust score

```
trust_score(claim) = base_confidence(source_type) × verification_multiplier
```

- `base_confidence`: `github` and `semantic_scholar` = 0.90; `hn` = 0.60;
  `tavily` and `serpapi` = 0.50; `deck` = 0.40.
- `verification_multiplier`: `verified=1.0`, `unverifiable=0.5`,
  `contradicted=0.0` (flagged, not counted).

Denormalized onto every `Claim` row at insert time so memo rendering is
a plain JOIN, no runtime math. Rendered as 🟢/🟡/🔴 badges everywhere.

### Three-axis screening — never averaged

- **Founder axis** = 0.9 × FounderScore + 10 × (is_technical bonus)
- **Market axis** = MarketResearch.stance → score, minus competitor crowd
  penalty
- **Idea vs Market axis** = if founder ≫ market by ≥ 35 pts, "team over
  idea"; if market ≫ founder by same, "market over team"; else
  `0.5×(founder+market)` and "balanced"

Each stored on its own row in `axis_score`, each with a trend arrow
computed against the previous `score_history` entry.

### Thesis Engine

`ThesisConfig` is a singleton row edited through the Streamlit sidebar.
`ThesisFit` is computed per-founder: matches on sector, geography, stage
(when known), check size. **Empty founder-side data is treated as
"unknown, allow through"** — consistent with the empty-results-are-
evidence invariant. Edit the sidebar → `recompute_all_thesis_fits()` fires
→ every founder's badge flips in place.

---

## The Streamlit app

Multi-page app under `ventureos_ui/pages/`:

### `00_Apply.py`
Upload a deck (PDF, TXT, or Markdown), or paste application text. Runs
`asyncio.run(graph.ainvoke(state))` synchronously with a live
`st.status` progress log. On success, saves the JSON to
`demo_data/inbound/<timestamp>_<slug>.json`, loads it into the DB, and
renders the agent trail inline (compact) so the user sees the pipeline's
decisions immediately.

### `01_Dashboard.py`
Founder list with `Company`, `Founder`, `Score ± CI`, cold-start
marker (❄️), Founder axis, Market axis, Idea vs Market, Screen, Thesis,
Source, Outreach flag. Sortable, click-to-navigate to profile.

### `02_Founder_Profile.py`
The visual center of the app. Six tabs:
- **Claims** — every extracted claim with predicate, source, trust
  badge, and a clickable "↗ open" link to the evidence.
- **Evidence sources** — every raw API response, clickable.
- **Market research** — stance, market size, competitors, reasoning.
- **🎯 SWOT** — 2-column layout with citation-backed bullets, each
  cited to a real URL.
- **Outreach draft** — the generated cold email (if any).
- **🧠 Agent Trace** — the full `reasoning_log` + `trace` timeline. Each
  node expandable to show metrics (evidence_count, claims_out, tavily
  fallbacks, etc.), plus any non-fatal errors that scoped to that node.

Also on the page:
- Score metric card with delta caption for cold-start.
- Component breakdown (TrackRecord / ExecutionSignal / NarrativeQuality
  / Consistency) with the actual weights used.
- Plotly score-history line chart showing all 4 axes over time.
- Attributes table (only shows fields with values; N-not-disclosed
  summary underneath).
- "↻ Recompute score + axes + memo" button — regenerates without
  re-running the pipeline.

### `03_Memo.py`
Renders the 5-section Markdown memo with a "Download .md" button. The
memo body is generated deterministically by `memo_builder.py` — no LLM
call. Sections:

1. **Company Snapshot** — only disclosed fields shown, with a compact
   "N field(s) not disclosed" summary line.
2. **Hypotheses** — top 5 verified claims with trust badges.
3. **SWOT (citation-backed)** — every bullet cites its source URL.
4. **Problem / Product** — narrative claims.
5. **Traction** — table with metric, value, source, trust badge,
   verification status.

Plus **Score Breakdown**, **Three-Axis Screening**, **Market Research**
(with clickable competitor URLs), **Draft Outreach**, **Evidence
Manifest**, a small **Flagged Contradictions (appendix)** when any
exist, and a **Decision Trail** listing every node's reasoning and
timing.

### `04_Query.py`
Natural-language query bar. Types like *"technical founder in Berlin
building AI infra, enterprise customers, no prior VC backing"* get
parsed by an LLM into a `QueryFilter` (from `ventureos.models`), then
applied against the DB. Pill badges show exactly how the LLM
interpreted the query, so you can always see the filter.

---

## Data flow: JSON → SQLite → UI

### Two data stores

1. **Tool + LLM cache** (`ventureos/cache.py`, backed by
   `.cache/ventureos.db`) — the LangGraph pipeline caches every
   external API response and every LLM call, keyed by hash of query +
   tool + prompt. Re-running the same founder against warmed caches is
   <2 seconds instead of ~40 seconds cold.

2. **UI DB** (`ventureos_ui/db.py`, backed by
   `.cache/ventureos_ui.db`) — 13 SQLAlchemy 2.0 tables:

| Table | Rows per founder | Purpose |
|---|---|---|
| `founder` | 1 | Root aggregate |
| `evidence_item` | ~5–15 | Raw API responses (JSON) |
| `claim` | ~15–40 | Extracted structured facts with trust scores |
| `contradiction` | 0–3 | Flagged cross-source mismatches |
| `market_research` | 0–1 | Stance, competitors, market size, reasoning |
| `swot_entry` | 4–12 | Citation-backed SWOT bullets |
| `founder_score` | 1 | Current 4-tier weighted score + CI |
| `axis_score` | 3 | Founder / Market / Idea-vs-Market |
| `score_history` | Grows over time | Append-only trend series |
| `thesis_config` | 1 (singleton) | Current fund thesis |
| `thesis_fit` | 1 | Computed in/outside thesis per founder |
| `memo` | 1 | Cached rendered Markdown |
| Plus indexes on hot columns |

Everything on the `founder` row includes the pipeline's own audit trail:
`intake` blob, `attributes` blob, `reasoning_log`, `trace`, `errors` —
so the Agent Trace tab in the UI can render exactly what the pipeline
thought without re-running it.

### Loader idempotency

`ventureos_ui/loader.py:load_founder_json()`:
- UPSERTs Founder / EvidenceItem / Claim / MarketResearch / SWOTEntry by
  primary key.
- Wipes and replaces Contradiction and SWOTEntry rows (they don't have
  stable IDs across pipeline runs).
- Appends to `score_history` (never overwrites — that's how the trend
  chart accumulates points).

Reloading the same JSON file twice leaves the DB in the same state,
except for one new `score_history` row per axis per reload.

---

## Deployment

### Render.com (recommended)

Full walk-through in [`DEPLOY_RENDER.md`](./DEPLOY_RENDER.md). Summary:

1. Push repo to GitHub.
2. Render Dashboard → New + → Blueprint → point at your repo.
3. Render detects [`render.yaml`](./render.yaml) automatically.
4. Fill in the 5 secrets in the Environment tab.
5. First deploy takes ~5-8 minutes.

Cost: **~$7.25/month** (Starter Web Service + 1 GB persistent disk) +
API costs.

The Blueprint mounts a persistent disk at `/data`, and both SQLite files
are symlinked onto it via [`scripts/render_start.sh`](./scripts/render_start.sh).
The startup script also auto-loads `demo_data/**/*.json` if the DB is
empty, so a first deploy comes up with demo data ready.

### Docker (any platform)

The `render_start.sh` entrypoint is generic enough to run on Fly.io,
Railway, or any Docker host. Full Dockerfile not committed — build one
like:

```dockerfile
FROM python:3.11-slim
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
EXPOSE 8501
CMD ["bash", "scripts/render_start.sh"]
```

Set `VENTUREOS_DATA_DIR=/data` and mount a volume there.

### Local dev

Just `uv run streamlit run ventureos_ui/app.py`. SQLite files live under
`.cache/` next to the repo.

---

## Cost and rate limits

### Per-founder API cost

| API | Calls | Cost |
|---|---|---|
| OpenAI (`gpt-4o-mini` for extraction, `gpt-4o` for reasoning) | ~15-20 | $0.03-0.06 |
| Tavily Search + Extract | ~5-7 | $0.005 |
| SerpAPI Google + Google AI Mode | ~4-6 | $0.02-0.03 |
| GitHub REST | ~3-5 | free (with token) |
| HN Algolia | ~2 | free |
| Semantic Scholar | ~2 | free |
| **Total** | ~35 | **~$0.10** |

For a batch of 20 founders: **~$2**. All calls cache in SQLite, so
re-runs of the same founder are ~$0 and <2 seconds.

### Rate limit sensitivities

- **OpenAI**: no issues at demo scale. If you push above 1 req/sec,
  tenacity retries handle transient 429s.
- **GitHub**: 5,000 requests/hour with a token; the pipeline uses <10 per
  founder.
- **SerpAPI**: 100 free searches/month, then $0.005/each on paid plans.
  Google AI Mode counts as 1 credit per call.
- **Tavily**: 1,000 free credits/month; a founder uses ~7.

---

## Extending the pipeline

### Add a new data source

1. Write a tool function in `ventureos/tools/<source>.py` returning
   `list[EvidenceItem]`.
2. Wire it into the `sourcing_node`'s parallel fan-out
   (`ventureos/nodes/sourcing.py`).
3. Add source-specific extraction prompt at
   `ventureos/prompts/extraction_<source>.md`.
4. Add `<source>` to `BASE_CONFIDENCE` in `ventureos_ui/scoring/constants.py`.

### Add a new scoring component

1. Define the predicate set (which claim predicates feed it) in
   `constants.py`.
2. Add a `_component_score()` call in `founder_score.py`.
3. Update the weights dict + cold-start reweighting rule.
4. Add the new row to the profile page's Component Breakdown expander.

### Add a new Streamlit page

Just drop a file in `ventureos_ui/pages/`. Numbered prefix controls
sidebar order (`05_XXX.py`). Call `bootstrap()` +
`render_sidebar_thesis_editor()` + `render_sidebar_data_ops()` at the
top for consistent state.

### Add a new pipeline node

1. Write it in `ventureos/nodes/<name>.py` — take `GraphState`,
   return a partial state dict.
2. Wrap the body in `with node_trace(state, "<name>") as t:` to get
   automatic timing + trace + reasoning-log wiring.
3. Register it in `ventureos/graph.py:build_graph()` — add node +
   edges.
4. If it writes to a new data type, add the Pydantic model to
   `ventureos/models.py` and a matching ORM table in
   `ventureos_ui/models_orm.py`.

---

## Troubleshooting

### "No founders in the DB"
Click **Reload demo_data/*** in the sidebar, or run
`uv run python -m ventureos_ui.loader demo_data`.

### Pipeline hangs or times out
Check `.env` for missing API keys. `env` config warnings are also
printed to stderr on the startup.

### "SSL passed invalid argument" on Tavily
Rate limit or connection drop. `tavily_tool.py` serializes calls with
a semaphore to avoid this — if it still happens, add explicit retries
via `tenacity`.

### Old data / stale scores
Click **↻ Recompute score + axes + memo** on the founder profile page.
For a full reset: `uv run python -m scripts.load_demo_20 --yes`.

### Cost creep
Everything caches in SQLite. If you're seeing repeat OpenAI calls,
check that `.cache/ventureos.db` is being persisted (on Render this
requires the disk mount).

To purge only the LLM cache while keeping tool responses:
```bash
uv run python -c "
import asyncio, aiosqlite
from ventureos.config import CACHE_PATH
async def clear():
    async with aiosqlite.connect(str(CACHE_PATH)) as db:
        cur = await db.execute(\"DELETE FROM cache WHERE key LIKE 'llm:%'\")
        await db.commit()
        print(cur.rowcount)
asyncio.run(clear())"
```

### Contradictions appearing incorrectly
Look at the Agent Trace tab → Verification node. The reasoning entry
tells you exactly which predicate groups were compared. The prompt
lives at [`prompts/verification.md`](./ventureos/prompts/verification.md)
and is hot-swappable.

---

## Contributing / design principles

1. **Empty results are evidence.** Never fill missing data with `false`
   or `0`. Use `null` and render `[Not Disclosed]`.
2. **Every claim cites its source.** The `source_evidence_id` chain
   must never break.
3. **Cost control > completeness.** Prefer 3 well-verified claims over
   30 unverified ones.
4. **Cache aggressively.** Anything that touches a paid API goes
   through `call_with_cache`.
5. **Prompts are hot-swappable.** No prompt lives in Python code.
6. **UI never mutates pipeline state.** UI only reads from the DB;
   pipeline only writes; loader is the bridge.

---

## Licence

MIT. See [`.gitignore`](./.gitignore) and enjoy.