# Deploying VentureOS to Render.com

This app is set up for a one-Blueprint deploy on **Render.com**. Everything the
platform needs to know is in [`render.yaml`](./render.yaml); this doc covers
the manual steps you can't put in a config file.

---

## Prerequisites

- A **Render.com** account (free to sign up at https://render.com).
- This repo pushed to **GitHub** (Render pulls from GitHub / GitLab / Bitbucket).
- API keys ready for the 5 secrets we'll set:
  - `OPENAI_API_KEY`
  - `TAVILY_API_KEY`
  - `SERPAPI_API_KEY`
  - `GITHUB_TOKEN`
  - `SEMANTIC_SCHOLAR_API_KEY`

---

## Step 1 — Push the repo to GitHub

```bash
git add render.yaml scripts/render_start.sh DEPLOY_RENDER.md
git commit -m "Add Render.com deployment blueprint"
git push origin main
```

If your default branch is not `main`, edit `render.yaml`'s `branch:` line to
match.

---

## Step 2 — Create the Blueprint on Render

1. Log into https://dashboard.render.com.
2. Click **New +** → **Blueprint**.
3. Connect your GitHub account if you haven't already, then select this
   repo (`VentureOS`).
4. Render detects `render.yaml` at the repo root. Click **Apply**.
5. Render will show a plan preview: one Web Service (`ventureos`) on the
   Starter plan ($7/mo), one 1 GB Persistent Disk (`$0.25/mo`), and 5
   secrets flagged as "Set at first deploy".
6. Fill in the 5 secret values when prompted, then click **Apply**.

Render begins the first build. Grab a coffee — it takes about **5-8 minutes**
(`uv sync --frozen --no-dev` is the slow step).

---

## Step 3 — Watch the first deploy

- Render Dashboard → the `ventureos` service → **Logs** tab.
- You should see the build finish with `Build succeeded`, then:

  ```
  [render_start] VENTUREOS_DATA_DIR=/data PORT=10000
  [render_start] Linked .cache → /data/.cache
  [render_start] DB initialized.
  [render_start] Founder table empty — auto-loading demo_data/*.json
  [render_start] Auto-loaded 9 founders.
  [render_start] Launching Streamlit on 0.0.0.0:10000
  ```

- After that, Render marks the service **Live** and gives you a URL like:

  ```
  https://ventureos.onrender.com/
  ```

Open it. You should see the dashboard with the demo founders already loaded.

---

## Step 4 — (Optional) Add a custom domain

- Render Dashboard → your service → **Settings** → **Custom Domains** →
  add your domain and follow the DNS instructions.
- Free HTTPS via Render-managed Let's Encrypt certificate.

---

## What lives where

| Location on Render | What it is |
|---|---|
| `/data/.cache/ventureos_ui.db` | UI SQLite DB — founders, scores, thesis config, memos |
| `/data/.cache/ventureos.db` | LangGraph tool cache (GitHub / HN / Tavily / SerpAPI / LLM) |
| `/opt/render/project/src/` | Working directory. `demo_data/`, `ventureos/`, `ventureos_ui/` all sit here. |
| Environment vars | Set in Render Dashboard → Environment. Secrets never touch the repo. |

The persistent disk (`/data`) is what makes this work — without it, both
SQLite files would reset on every deploy. **Never delete the disk**; if you
do, all founders + scoring history + tool cache are gone.

---

## Cost breakdown

| Item | Monthly |
|---|---|
| Web Service (Starter plan, always-on) | $7 |
| Persistent Disk (1 GB) | $0.25 |
| **Render subtotal** | **~$7.25** |
| OpenAI + Tavily + SerpAPI (usage-based) | $1-3/day of active use |

The free tier works for demos (spins down after 15 min of idle, no disk), but
loses all data on redeploy. If you want free, edit `render.yaml`:

```yaml
plan: free
# ... and remove the disk: block entirely
```

---

## Updating / redeploying

Every push to `main` triggers an automatic redeploy. Data on the persistent
disk survives.

To force a redeploy without a code change:
- Dashboard → service → **Manual Deploy** → **Deploy latest commit**.

---

## Common issues

**`Application failed to respond` on first load after idle**
The Starter plan is always-on, so this shouldn't happen. If you're on the free
plan, this is the cold-start (~30-60s). Just refresh once and it comes up.

**Environment secret not set**
Dashboard → service → **Environment** → click any secret → paste the value → **Save**. The service auto-redeploys.

**Streamlit shows "Please wait…" forever**
Check the Logs tab. Almost always means an environment variable is missing (typically an API key) and the pipeline is failing on cold start. The Auto-load step in `render_start.sh` is best-effort — if it fails, the service still starts and you'll see the failures under **Logs**.

**Disk fill-up**
The tool cache grows over time. On the Dashboard → service → **Shell** → run:
```bash
du -sh /data/.cache/*
```
To rotate the tool cache (keeps user data intact):
```bash
uv run python -c "
import asyncio, aiosqlite
from ventureos.config import CACHE_PATH
async def clear():
    async with aiosqlite.connect(str(CACHE_PATH)) as db:
        cur = await db.execute(\"DELETE FROM cache WHERE fetched_at < strftime('%s', 'now', '-30 days')\")
        await db.commit()
        print(cur.rowcount, 'stale entries cleared')
asyncio.run(clear())
"
```

---

## Rolling back

Dashboard → service → **Events** → find a previous successful deploy → click
**Rollback to this deploy**. Persistent disk data stays put.

---

## Migrating to Postgres later (optional)

SQLite is fine for one active user. If you expect multiple concurrent users
hitting the Apply page:

1. Render Dashboard → **New +** → **PostgreSQL**. Pick the smallest paid tier
   (~$7/mo, 256 MB).
2. Add a `DATABASE_URL` env var to the `ventureos` service, using
   `fromDatabase` in `render.yaml`:

   ```yaml
   - key: DATABASE_URL
     fromDatabase:
       name: ventureos-pg
       property: connectionString
   ```

3. `ventureos_ui/db.py` already respects `DATABASE_URL` — flip the env var,
   redeploy, done. Existing SQLite data will not migrate automatically; you'd
   need to re-run `scripts/load_demo_20.py` against the new DB.

---

## Fully automated setup for a fresh copy

Once you've done the above once, cloning this repo to a new Render workspace
is:

1. Fork the repo on GitHub.
2. Render Dashboard → **New +** → **Blueprint** → point at your fork.
3. Fill in the 5 secrets on first deploy.
4. Done.

No code changes required.