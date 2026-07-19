#!/usr/bin/env bash
#
# Render.com cron entrypoint for scheduled outbound scans.
#
# Fires by default once a week (Sunday 06:00 UTC per render.yaml). Writes
# JSONs to the SAME persistent disk that the web service reads from, so the
# newly-discovered founders show up in the dashboard automatically on the
# next request.
#
# Configuration via env vars (all set in render.yaml):
#   VENTUREOS_DATA_DIR       — where SQLite files + demo_data live (/data)
#   OUTBOUND_HOURS           — look-back window (default 168 = 7 days)
#   OUTBOUND_PER_SOURCE      — max candidates discovered per source (default 5)
#   OUTBOUND_LIMIT           — total candidates to run through pipeline (default 5)
#   OUTBOUND_DEVPOST_LIMIT   — devpost-specific cap (default 5)

set -euo pipefail

DATA_DIR="${VENTUREOS_DATA_DIR:-/data}"
HOURS="${OUTBOUND_HOURS:-168}"
PER_SOURCE="${OUTBOUND_PER_SOURCE:-5}"
LIMIT="${OUTBOUND_LIMIT:-5}"
DEVPOST_LIMIT="${OUTBOUND_DEVPOST_LIMIT:-5}"

echo "[cron_outbound] Starting scheduled outbound scan at $(date -u +'%Y-%m-%d %H:%M:%S UTC')"
echo "[cron_outbound] DATA_DIR=$DATA_DIR HOURS=$HOURS LIMIT=$LIMIT PER_SOURCE=$PER_SOURCE"

# Symlink .cache/ → persistent disk (mirrors render_start.sh)
mkdir -p "$DATA_DIR/.cache"
if [ ! -L .cache ]; then
    rm -rf .cache 2>/dev/null || true
    ln -s "$DATA_DIR/.cache" .cache
fi

# Also mirror demo_data → persistent disk so outbound JSONs land where the web
# service will see them
mkdir -p "$DATA_DIR/demo_data"
if [ ! -L demo_data ]; then
    # Preserve any curated JSONs shipped with the repo on first cron run
    if [ -d demo_data ] && [ ! -L demo_data ]; then
        cp -rn demo_data/. "$DATA_DIR/demo_data/" 2>/dev/null || true
        rm -rf demo_data
    fi
    ln -s "$DATA_DIR/demo_data" demo_data
fi

# Run the outbound scan → writes to demo_data/outbound/
uv run python -m scripts.outbound_scan \
    --hours "$HOURS" \
    --per-source "$PER_SOURCE" \
    --devpost-limit "$DEVPOST_LIMIT" \
    --limit "$LIMIT" \
    --out-dir demo_data/outbound

# Load new JSONs into the DB (idempotent — existing founders won't be
# duplicated). We load the whole demo_data tree to catch new files.
echo "[cron_outbound] Loading new outbound JSONs into DB..."
uv run python -m ventureos_ui.loader demo_data/outbound

echo "[cron_outbound] Done at $(date -u +'%Y-%m-%d %H:%M:%S UTC')"