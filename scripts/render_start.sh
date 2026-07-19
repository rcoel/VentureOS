#!/usr/bin/env bash
#
# Render.com startup entrypoint.
# Handles:
#   1. Redirecting .cache/ onto the persistent disk mount ($VENTUREOS_DATA_DIR).
#   2. Initializing the UI DB (idempotent — safe on every boot).
#   3. Optionally auto-loading demo_data JSONs on first boot.
#   4. Launching Streamlit bound to $PORT / 0.0.0.0.

set -euo pipefail

DATA_DIR="${VENTUREOS_DATA_DIR:-/data}"
PORT="${PORT:-8501}"

echo "[render_start] VENTUREOS_DATA_DIR=$DATA_DIR PORT=$PORT"

# 1a. Symlink .cache → persistent disk (preserves both SQLite files across deploys).
mkdir -p "$DATA_DIR/.cache"
if [ ! -L .cache ]; then
    if [ -d .cache ] && [ ! -L .cache ]; then
        cp -rn .cache/. "$DATA_DIR/.cache/" 2>/dev/null || true
        rm -rf .cache
    fi
    ln -s "$DATA_DIR/.cache" .cache
    echo "[render_start] Linked .cache → $DATA_DIR/.cache"
fi

# 1b. Symlink demo_data → persistent disk (shared with the cron service so
#     scheduled outbound scans write JSONs where the web service reads them).
mkdir -p "$DATA_DIR/demo_data"
if [ ! -L demo_data ]; then
    if [ -d demo_data ] && [ ! -L demo_data ]; then
        # Seed the disk with any curated JSONs from the git repo on first boot
        cp -rn demo_data/. "$DATA_DIR/demo_data/" 2>/dev/null || true
        rm -rf demo_data
    fi
    ln -s "$DATA_DIR/demo_data" demo_data
    echo "[render_start] Linked demo_data → $DATA_DIR/demo_data"
fi

# 2. Initialize the UI DB (creates tables if they don't exist).
uv run python -c "
from ventureos_ui.db import init_db
from ventureos_ui.loader import ensure_default_thesis, get_session
init_db()
with get_session() as s:
    ensure_default_thesis(s)
print('[render_start] DB initialized.')
"

# 3. Auto-load demo_data on first boot if the DB is empty.
if [ "${VENTUREOS_AUTOLOAD_ON_EMPTY:-false}" = "true" ]; then
    uv run python -c "
from sqlalchemy import select, func
from ventureos_ui.db import get_session
from ventureos_ui.models_orm import Founder
from ventureos_ui.loader import load_dir
from pathlib import Path

with get_session() as s:
    n = s.scalar(select(func.count()).select_from(Founder)) or 0

if n == 0 and Path('demo_data').exists():
    print('[render_start] Founder table empty — auto-loading demo_data/*.json')
    with get_session() as s:
        ids = load_dir(Path('demo_data'), session=s)
    print(f'[render_start] Auto-loaded {len(ids)} founders.')
else:
    print(f'[render_start] {n} founders already in DB — skipping auto-load.')
"
fi

# 4. Launch Streamlit on the port Render assigns.
echo "[render_start] Launching Streamlit on 0.0.0.0:$PORT"
exec uv run streamlit run ventureos_ui/app.py \
    --server.address 0.0.0.0 \
    --server.port "$PORT" \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false