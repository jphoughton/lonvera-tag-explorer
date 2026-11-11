# Agent guide: Lonvera Tag Explorer / Marketing metrics / Projections / Production board

Orientation for a brand-new AI agent or colleague. Fast-scan reference, not a tutorial. Facts here were checked against the repo on branch `feat/priorities-production` (tip fb3942d, 2026-11-10). Anything not checkable from the repo is marked UNVERIFIED.

## TLDR

- One Flask app (`app/`) plus one Postgres database, one Docker image, several Railway services (`web` plus cron jobs that run `python -m etl.<job>`).
- Four things live in the same app: **Tags / Marketing metrics** (Meta and Google ad numbers by product and tag), **Projection** (the P&L and cash model that replaced the Lonvera Projections sheet), **Creative priorities** (`/priorities`, a weekly list of which ad concepts to make), and the **Production board** (`/priorities/production`, who is making which concept).
- The app reads Meta, Upstack, Google Sheets (Creative Tracker) and other feeds. Meta writes exist only in `etl/meta_write.py`, create PAUSED objects only, and are switched off (dark). No real Meta write has ever been made.
- Default branch `build/v1`. Never commit to it directly: branch, pull request, merge. Tests green on the exact tip means merge, deploy and verify live (this app only).
- Test suite: 8 to 9 minutes, run one at a time. Command in section 8.
- Read-only data access for agents: the `analytics` database views (section 4) or the HTTP API with an API key (section 5).

Live URL: https://web-production-358da.up.railway.app

## 1. What the app is, who uses it, where it runs

- **Users.** Josh Houghton (owner) and named colleagues with logins (`APP_USERS`). Vendors (Madre, Victoria, and others) use only their own link page. Colleagues or scripts can use a scoped API key.
- **Project.** Railway project `lonvera-tag-explorer`, id `5ea06da2-cc55-4bb7-ac5c-8462d0d95338`, environment `production`. Source: `docs/DEPLOY.md`. (Do not call Railway to check; the table below comes from the in-repo scripts and docs.)
- **One image.** `Dockerfile` builds web and every cron service. The start command differs per service. Config-as-code in `railway.json` is only a human reference; the real settings are service settings applied by `scripts/deploy/apply_*_service.py` (dry run by default).

| Service | What it does | Start command | Schedule (UTC) | Source |
|---|---|---|---|---|
| `web` | Flask API and the single-page app (gunicorn, 2 workers x 8 threads), healthcheck `/healthz` | image default `scripts/deploy/start_web.sh` (runs `python -m etl.migrate`, then gunicorn) | always on | `Dockerfile`, `railway.json` |
| `etl` | Nightly refresh, in order: `etl.run_daily`, `etl.upstack_daily`, `etl.stage_moves`, `etl.projection_daily`, `etl.amazon_mtd`, then non-fatal: `etl.competitor_ads --if-sunday`, `etl.creative_priorities --if-sunday`, `etl.assignment_tags`, `etl.auto_tag` | `scripts/deploy/run_etl.sh` | `0 13 * * *` | `run_etl.sh`, `apply_service_config.py` |
| `feeds` | Pulls Upstack, Warehance, Meta reach and ExpandFi feeds into `proj_feedx_*` and `proj_feed_*` tables, then `etl.amazon_mtd`, then `etl.feeds verify` | `scripts/deploy/run_feeds.sh` | `0 21 * * *` | `apply_feeds_service.py` |
| `live` | Hourly Upstack pull of the Pacific day so far into `proj_live_today_snapshots` | `scripts/deploy/run_live.sh` (`python -m etl.live_today`) | `0 * * * *` (from project notes; the repo has no `apply_live_service.py`, so UNVERIFIED in-repo) | `run_live.sh`, `etl/live_today.py` |
| `alerts` | Hourly read-only health checks, one Slack message per problem | `scripts/deploy/run_alerts.sh` | `0 * * * *` | `apply_alerts_service.py`, `docs/ALERTS.md` |
| `backup` | Nightly `pg_dump` to Google Drive, restore check, prune. Own image `scripts/deploy/backup/Dockerfile` | `scripts/deploy/run_backup.sh` | `30 8 * * *` | `apply_backup_service.py`, `docs/BACKUP.md` |
| `Postgres` | Railway Postgres 18, private network | template | always | `docs/DEPLOY.md` |

- UNVERIFIED: whether `alerts` and `backup` exist in production. `docs/BACKUP.md` (2026-11-09) says the backup service was "not created or deployed yet"; `docs/ALERTS.md` describes creating `alerts` as a step.
- **Launch worker** (`scripts/deploy/run_launch_worker.sh`, `python -m etl.launch_worker`): code exists, wired to NO service. See section 6.
- Cron services only run at their next tick after a deploy. A deploy does not start a run.

## 2. Repo map

| Path | What lives there |
|---|---|
| `app/` | Flask API. `app/__init__.py` is the app factory and registers the page routes |
| `app/static/` | The single-page app: `index.html`, `app.js` (route table), `app.css`, `vendor/` (Chart.js is vendored) |
| `app/static/js/pages/` | One file per page (section 3). Shared helpers in `app/static/js/*.js` (`api.js`, `table.js`, `charts.js`, ...) |
| `app/static/projection*`, `app/static/js/projection/` | The Projection page: `projection.html`, shell and one JS file per module |
| `app/projection/` | Projection backend: core (`api.py`, `engine.py`, `scenarios.py`) plus one sub-package per module (cohorts, drivers, financing, inventory_demand, inventory_supply, modeler, pacing, pnl, variables) |
| `etl/` | Jobs, each run as `python -m etl.<name>` |
| `etl/feeds/` | The feeds service: `upstack.py`, `warehance.py`, `meta_feeds.py`, `expandfi_feeds.py`, `verify.py`, CLI in `__main__.py` |
| `etl/autotag/` | Helpers for the auto-tagger |
| `migrations/` | Numbered SQL files applied by `etl.migrate` |
| `scripts/deploy/` | Service start scripts and `apply_*_service.py` setup scripts |
| `scripts/gen_schema_doc.py`, `scripts/auth/hash_password.py`, `scripts/projection/` | Schema doc generator, login hash tool, projection reconcile and snapshot tools |
| `tests/` | `api/`, `etl/`, `alerts/`, `ui/` (browser tests against a mock server), `projection/` (subfolders per module), plus two root tests |
| `docs/` | All documentation (index below). `docs/projection/` holds the Projection design, module docs and QA reports |
| `analysis/` | One-off analysis material, not part of the running app |

### Key modules

| Module | One line |
|---|---|
| `app/api.py` | Most HTTP routes: products, rollups, trees, campaigns, platforms, promotions, priorities, trends, ad lookup, media, previews |
| `app/auth.py` | `APP_USERS` logins, session cookie `tagexp_session`, request guard, API key check, `PUBLIC_API_RE` for vendor links |
| `app/api_keys.py` | Scoped API keys (`lvk_` prefix, hash only stored). Added by migration 204 |
| `app/config.py` | All settings from environment variables |
| `app/db.py` | psycopg connection pool, one read-only transaction per request |
| `app/queries.py` | All SQL for the Tag Explorer API (about 3,000 lines) |
| `app/respcache.py` | In-process cache of finished metrics responses, cleared when the nightly ETL changes the data |
| `app/platforms.py`, `app/google.py`, `app/trends.py` | All-platforms overview, Google pages, Trends page |
| `app/promotions.py`, `app/scale_views.py` | Test to scale (`/promotions`) |
| `app/lookup.py`, `app/preview.py` | Tag lookup by ad name; server-side ad preview iframe URL |
| `app/creative_priorities.py` | Reads the stored weekly snapshot for `/api/priorities` |
| `app/priorities_manual.py` | Manual concepts on `/priorities` |
| `app/priorities_assign.py` | Production assignments, vendors, vendor links, stage rules |
| `app/priorities_launch.py` | Launch logic, pure functions only (no database, no network) |
| `app/priorities_launch_api.py` | Launch routes behind three gates (dark) |
| `app/projection/` | Projection backend, routes under `/api/projection/` |
| `etl/run_daily.py` | Tracker sync, Meta pull, rebuild of derived tables, atomic |
| `etl/tracker_sync.py` | Creative Tracker sheet to `tracker_rows` and tracker tags (read-only from the sheet) |
| `etl/meta_api.py` | Read-only Meta Graph client (GET only, retries on rate limits, scrubs tokens) |
| `etl/meta_write.py` | The ONLY module allowed to write to Meta; creates PAUSED objects only (dark) |
| `etl/drive_io.py` | Read-only Google Drive access (`drive.readonly` scope) for the launch worker |
| `etl/sheets_io.py` | Read-only Google Sheets reader (snapshot dir or live, never writes) |
| `etl/launch_worker.py` | Turns queued launch jobs into a plan or PAUSED Meta ads. Dark, not wired to any service |
| `etl/creative_priorities.py` | Builds the weekly priorities snapshot. Thresholds are constants at the top |
| `etl/assignment_tags.py` | Nightly: gives ads linked to an assignment that assignment's line tags |
| `etl/auto_tag.py` | Nightly auto-tagging of untagged ads with headless Claude |
| `etl/load_taxonomy.py`, `etl/load_tags.py`, `etl/load_hooks.py` | Load the Communication Overview taxonomy, tagging results, ad hooks |
| `etl/build_metrics.py` | Rebuilds `family_daily`, `ad_metrics` and friends in one transaction |
| `etl/upstack_*.py` | Upstack source (new customer revenue, Google attribution, orders) |
| `etl/stage_moves.py` | Test to scale tables |
| `etl/projection_*.py` | Projection daily refresh hooks per module |
| `etl/competitor_ads.py` | Weekly public Meta Ad Library scrape plus classification |
| `etl/alerts.py`, `etl/backup.py` | Hourly alerts; nightly database backup |
| `etl/reader_role.py` | Creates the read-only `analytics_reader` login |
| `etl/api_keys.py` | `create`, `list`, `revoke` API keys |
| `etl/migrate.py` | Migration runner |

### Docs index

`docs/CONTRACT.md` (metric rules), `DEPLOY.md`, `DATA_SCHEMA.md` (generated), `AGENT_DATA_GUIDE.md` (read-only SQL quick start), `CREATIVE_PRIORITIES.md` (priorities, production, API keys), `UPSTACK_SOURCE.md`, `TEST_TO_SCALE.md`, `TRENDS.md`, `AUTO_TAG.md`, `TAG_LOOKUP.md`, `ALERTS.md`, `BACKUP.md`, `COMPETITOR_ADS.md`, `AMAZON_MTD.md`, `PLATFORMS.md`, `projection/*`. There is no `docs/PRODUCTION_*.md`; production board documentation is the "Production" section of `docs/CREATIVE_PRIORITIES.md`.

## 3. Pages and what serves them

The browser gets `app/static/index.html` for every page route (list in `app/__init__.py`, `_register_pages`). The JS route table is in `app/static/app.js`.

| Page | Route | JS file | Main API |
|---|---|---|---|
| All platforms (home when platform = all) | `/` | `pages/platforms.js` | `/api/platforms/overview`, `/api/platforms/kpis`, `/api/platforms/trend` |
| Products and tag tree (Meta home) | `/`, `/p/<slug>` | `pages/products.js`, `pages/product.js`, `tree.js` | `/api/products`, `/api/products/<slug>/tree` |
| Campaigns | `/campaigns` | `pages/campaigns.js`, `pages/all_campaigns.js` | `/api/campaigns`, `/api/campaigns/all` |
| Google | `/google` (redirects into `/?platform=google`) | `pages/google.js` | `/api/google/rollup`, `/api/google/trend` |
| Ad page | `/ad/<id>` | `pages/ad.js` | `/api/ads/<id>` |
| Test to scale | `/promotions` | `pages/promotions.js`, `pages/scale_views.js` | `/api/promotions`, `/api/promotions/scale` |
| Trends | `/trends` | `pages/trends.js` | `/api/trends`, `/api/trends/plan` |
| Tag lookup | `/lookup` | `pages/lookup.js` | `/api/tag-lookup` |
| Creative priorities | `/priorities` | `pages/priorities.js`, `pages/priorities_manual.js` | `/api/priorities`, `/api/priorities/manual`, `/api/priorities/views` |
| Saved view share page | `/priorities/v/<token>` | `pages/priorities.js` (`prioritiesViewPage`) | `/api/priorities/views/<token>` |
| Production board | `/priorities/production` | `pages/priorities_production.js` (`productionPage`), `pages/priorities_assign.js` | `/api/priorities/assignments`, `/api/priorities/vendor-links` |
| Launch panel inside the board (dark) | same | `pages/priorities_launch.js` | `/api/priorities/launch/*`, `/api/priorities/assignments/<id>/launch/*` |
| Vendor page (no login) | `/priorities/vendor/<token>` | `pages/priorities_production.js` (`vendorPage`) | `/api/priorities/vendor/<token>` |
| Projection | `/projection`, `/projection/<module>` | `app/static/projection.html`, `js/projection/shell.js` plus one file per module | `/api/projection/...` |
| Login | `/login` | `pages/login.js` | `/api/login` |

Projection modules (one folder each in `app/projection/`): cohorts, drivers, financing, inventory_demand, inventory_supply, modeler, pacing, pnl, variables. Their API prefix is `/api/projection/<module-with-dashes>/`. The catalog and nav order are in `app/projection/interfaces.py` (`MODULE_CATALOG`).

## 4. Data

Postgres only. No data is stored in files. `DATABASE_URL` points at it.

### Main tables by domain (names from `migrations/`)

| Domain | Tables |
|---|---|
| Meta ads and metrics | `meta_ads`, `meta_campaigns`, `ad_daily`, `ad_lifetime`, `ad_metrics`, `family_daily`, `family_daily_active`, `meta_family_map`, `family_content`, `creative_assets`, `unmatched_meta`, `ad_hooks` |
| Creative Tracker mirror | `tracker_rows` (read from the sheet), `ad_tags` (tag_set and version per ad) |
| Taxonomy | `taxonomy_lines` (cluster, ICP, pain point lines), `line_ingredients`, `products` |
| Upstack | `upstack_orders_daily`, `upstack_attr_daily`, `upstack_campaign_daily`, `upstack_channel_daily`, `ad_daily_upstack`, `upstack_google_*`, `google_daily` |
| Test to scale | `creative_stage_moves*`, `creative_scaling_placements*`, `scaling_benchmarks` |
| Projection | `proj_scenarios`, `proj_series`, `proj_actuals`, `proj_audit`, `proj_cohort*`, `proj_pnl_*`, `proj_po`, `proj_fba_transfer`, `proj_stock_snapshot`, `proj_units_*`, `proj_pacing_*`, `proj_amazon_mtd_snapshots`, `proj_live_today_snapshots`, `proj_feed_*`, `proj_feedx_*` |
| Creative priorities | `creative_priorities_snapshots`, `creative_priorities_views`, `creative_priorities_manual`, `creative_priorities_assignments`, `creative_priorities_vendor_links`, `competitor_brands`, `competitor_ads`, `competitor_concept_class` |
| Launch (dark) | `creative_launch_settings`, `creative_launch_pages`, `creative_launch_vendor_prefixes`, `creative_launch_drafts`, `creative_launch_jobs`, `creative_launch_ads`, `creative_launch_adsets`, `creative_launch_audit` |
| Access and ops | `api_keys` (migration 204), `etl_runs`, `app_meta`, `alert_state`, `schema_migrations` |

### Schema documentation

- Human and agent dictionary: `docs/DATA_SCHEMA.md`; machine readable `docs/schema.json`. Both are generated by `scripts/gen_schema_doc.py` from the curated text in `docs/schema/descriptions.py` (the only file people edit) plus the live schema.
- `python scripts/gen_schema_doc.py --write` regenerates; `--check` fails when stale; `--problems` lists undescribed entries; `--verify-live` compares against a database read-only. All need `DATABASE_URL` pointing at a database with every migration applied (a local scratch database is fine).
- `tests/api/test_schema_docs.py` fails when a new table or column has no description.

### Migration conventions

- Files `migrations/NNN_name.sql`, applied in filename order by `python -m etl.migrate`, tracked in `schema_migrations`. `start_web.sh` and every `run_*.sh` run it on start.
- An applied migration is immutable. Fix mistakes with a new, higher-numbered migration. Make migrations additive and idempotent (`CREATE TABLE IF NOT EXISTS`).
- Number ranges: 001 to 020 original app, 100 to 191 Projection and analytics views, 192 onward newer features.
- Which numbers exist where (checked 2026-11-10):

| Branch | Highest migration | Notes |
|---|---|---|
| `build/v1` (default) | 203 | Includes 202 (production assignments) and 203 (creative launch) |
| `feat/priorities-production` (integration) | 204 | 204 `api_keys` exists only here until the integration branch merges |

- If you add a migration on a branch, check the highest number on both branches first to avoid a number clash. (Number 014 does not exist; that gap is normal.)

### Read-only database login

- Role `analytics_reader` can SELECT from the `analytics` views and nothing else. Created by `python -m etl.reader_role`. Quick start for agents: `docs/AGENT_DATA_GUIDE.md` (start with `select * from analytics.data_coverage`).
- The connection URL is a secret and is NOT in this repo. The owner keeps it at `~/lonvera-tag-explorer-data/analytics_reader_url.txt` (mode 600). Never paste it into chat, a file in the repo or memory.

## 5. Auth and access

| Mechanism | How it works | Where |
|---|---|---|
| Named logins | `APP_USERS` env var, a JSON object `{"name": "<scrypt hash>"}`. Empty or invalid means nobody can log in. Hashes made with `scripts/auth/hash_password.py`. Signed session cookie `tagexp_session` (30 days), login throttling per IP and user | `app/auth.py`, `app/config.py` |
| Auth off switch | `AUTH_DISABLED=1` opens the whole app (user = "guest"). Do not set it in production | `app/config.py` |
| Vendor token links | One unguessable link per vendor. Only paths matching `PUBLIC_API_RE` (`/api/priorities/vendor/<token>` and `/<assignment id>`) skip login; nothing else is public | `app/auth.py`, `app/priorities_assign.py` |
| API keys | `Authorization: Bearer lvk_...`. Read everything, plus assign on the production board. Cannot launch, log in or read vendor link tokens. Make one with `python -m etl.api_keys create --name <who>` | See the "API keys" section of `docs/CREATIVE_PRIORITIES.md` (added by the API keys PR, #84, merged into `feat/priorities-production`) |
| Launch gate (dark) | Three gates in order: `META_LAUNCH_ENABLED` is exactly `1` (else 404), sign-in is on (else 403), the user is listed in `META_LAUNCH_USERS` (else 403). A key never counts. `META_LAUNCH_DRY_RUN` stays a dry run unless exactly `0` | `app/priorities_launch_api.py`, `app/priorities_launch.py` |

Write routes also check Origin, are rate limited per IP, and take JSON only. Every `/api/` and `/media/` path needs a login or key except the login routes and the vendor links.

### Reading data over HTTP

Read endpoints (all GET, grouped; generated from the Flask routes):

| Area | Routes |
|---|---|
| Status | `/api/status` |
| Products and tags | `/api/products`, `/api/products/<slug>/rollup`, `/families`, `/tree`, `/coverage`, `/ads`, `/campaigns`, `/trend`, `/stage-daily`, `/unmatched` |
| Ads | `/api/ads/<id>`, `/api/ads/<id>/preview`, `/api/meta-ads/<ad_id>/preview`, `/media/<asset_id>`, `/api/tag-lookup` |
| Campaigns and platforms | `/api/campaigns`, `/api/campaigns/trend`, `/api/campaigns/all`, `/api/platforms/kpis`, `/api/platforms/trend`, `/api/platforms/overview`, `/api/google/rollup`, `/api/google/trend`, `/api/source/tieout` |
| Landing pages | `/api/landing-pages`, `/api/landing-pages/<id>` (copy of each page the ads point to, with the top ads per page; see `docs/LANDING_PAGES.md`) |
| Test to scale and trends | `/api/promotions`, `/api/promotions/scale`, `/api/trends`, `/api/trends/plan` |
| Creative priorities | `/api/priorities?week=YYYY-MM-DD`, `/api/priorities.csv`, `/api/priorities/views` and `/views/<token>`, `/api/priorities/manual` and `/manual/options` |
| Production board | `/api/priorities/assignments`, `/api/priorities/vendor-links` (login only, keys refused), `/api/priorities/vendor/<token>` (public link), `/api/priorities/launch/settings`, `/api/priorities/assignments/<id>/launch` |
| Projection | `/api/projection/modules`, `/scenarios`, `/series`, `/audit`, `/freshness`, `/run`, then `/api/projection/<module>/...` (`pacing/month`, `pnl/...`, `financing/...`, `inventory-supply/...` and so on) |

Example (key in a shell variable, never typed into a file):

```bash
KEY=$(cat ~/my.key)        # a key made with python -m etl.api_keys create --out ~/my.key
curl -H "Authorization: Bearer $KEY" https://web-production-358da.up.railway.app/api/products
curl -H "Authorization: Bearer $KEY" "https://web-production-358da.up.railway.app/api/priorities?week=2026-11-08"
```

To see the full current list: `grep -rnE "@bp\.(get|post|put|patch|delete)" app`.

## 6. Production board (`/priorities` and `/priorities/production`)

Full spec: `docs/CREATIVE_PRIORITIES.md`. Short version:

**Creative priorities (`/priorities`).** A weekly snapshot, one row per concept (Market Cluster, ICP, Pain Point, Story), each with exactly one **Action** and one **Status**.

| Action (JSON `section`) | Rule in short |
|---|---|
| `revive` | Beat the product average 3+ weeks in a row, now below it or off |
| `make_more` | Status Working or Rising, ranked by score, no row cap |
| `test` | On our sheet with 0 to 1 ads run; cap 6 per product |
| `add` | Not on our sheet yet (competitor angle or our draft) |
| `stop` | Enough spend and consistently below average |
| `hold` | Everything else |

- Statuses: Working, Rising, Fading, Not working, Average, Too early, Untested. All thresholds are constants at the top of `etl/creative_priorities.py`; the page prints them.
- Old snapshot values (faded, iterate, new) still read correctly through the `norm_*` functions in `etl/creative_priorities.py`. Do not rewrite stored snapshots.
- Snapshot by hand: `python -m etl.creative_priorities --week <Sunday YYYY-MM-DD>` (add `--dry-run` to store nothing, `--out DIR` for CSV and markdown). The nightly etl runs it with `--if-sunday` only on Pacific Sundays. Table: `creative_priorities_snapshots` (one row per Sunday week; re-running replaces it).
- Saved views (share links by token), manual concepts, briefed marks are described in the same doc.

**Production board (`/priorities/production`).**
- An **assignment** = one concept given to one vendor for one week ("week of" = a Monday). Table `creative_priorities_assignments`; one link token per vendor in `creative_priorities_vendor_links`.
- Vendors (list `VENDORS` in `app/priorities_assign.py`, spelled as in the tracker Vendor column): Madre, Victoria, TrybeDCShop, TrybeOrion, 10xROAS, Internal, PuredMedia, CreateMastery, OpenProgram.
- Stages are never stored; `stage_of()` derives them on every read from the assignment's own facts and its linked Creative Tracker rows: Assigned, Brief sent, Creative delivered, Ready to launch, Launched, Dropped.
- Vendors on their link page see only their own assignments and can do a small fixed set of things (add or change the creative link first; exact list in `docs/CREATIVE_PRIORITIES.md`).
- Nightly `python -m etl.assignment_tags` (non-fatal, `etl_runs` job `assignment_tags`) gives each linked ad that has no current line tag the assignment's line. It never overwrites an existing tag.
- Nothing on the board writes to the Creative Tracker sheet or talks to Meta.

**Dark launch pipeline (launching approved creative as PAUSED Meta ads).**

| Piece | Exists? |
|---|---|
| Migration 203 (`creative_launch_*` tables) | Yes |
| `app/priorities_launch.py` (pure logic), `app/priorities_launch_api.py` (routes, three gates) | Yes |
| `app/static/js/pages/priorities_launch.js` (UI) | Yes |
| `etl/meta_write.py` (PAUSED-only Meta writer) and `etl/drive_io.py` (read-only Drive) | Yes |
| `etl/launch_worker.py` and `scripts/deploy/run_launch_worker.sh` | Yes in code (PR #72). Dark and mocked: refuses to run unless `META_LAUNCH_ENABLED=1` and `META_LAUNCH_TOKEN` is set, dry run by default, refuses to build real clients under pytest. NOT wired to any Railway service |
| A real Meta write ever made | No, never |

The earlier brief for this guide said the worker did not exist yet; it does exist on this branch and on `build/v1`. What does not exist is a Railway service running it, or any production `META_LAUNCH_*` setting (UNVERIFIED for production variables; the code defaults are all off).

## 7. Branches and deploy

- **Default branch:** `build/v1` (not `main`; `main` is only a skeleton).
- **Integration branch:** `feat/priorities-production`. At the time of writing it is 2 commits ahead of `build/v1` (both API keys) and 7 commits behind it (priorities hierarchy and Production detail panel work). It has NOT been merged into `build/v1`. Whether it has been deployed to production is UNVERIFIED; check Railway deployment history or `/healthz` before assuming. Migration 204 exists only on it.
- Open pull requests into `build/v1` at the time of writing: #78 (tagger pain rule), #81 (`ship.sh` parallel deploy script), #82 (optional creative format on an assignment), #83 (priorities grouped table). `ship.sh` is not merged, so do not rely on it.
- **Workflow:** branch, pull request (`gh --repo jphoughton/lonvera-tag-explorer pr create`), merge. Never commit or push directly to `build/v1` or `main`.
- **Standing order (Josh, 2026-11-04):** for this app only, once the full suite is green on the exact tip, merge, deploy and verify live without asking again. Verify means load the real URL, not just check logs.
- **Deploy command pattern** (service by service; deploy only what changed):

```bash
railway link -p lonvera-tag-explorer -e production        # once per directory (stored in ~/.railway)
git archive HEAD | tar -x -C <empty dir>                 # deploy a clean export of the exact commit
railway up <that dir> --path-as-root -p 5ea06da2-cc55-4bb7-ac5c-8462d0d95338 -e production -s web -c
```

  `-s` takes web, etl, feeds, live, alerts or backup. `backup` builds from the repo root with its own Dockerfile. The `git archive` plus `--path-as-root` form is the pattern Josh uses (UNVERIFIED in-repo: `docs/DEPLOY.md` still shows the older `railway up -s web -c` from a working copy).
- `start_web.sh` runs `python -m etl.migrate` before gunicorn, so a web deploy applies new migrations. Rolling back web does not undo migrations.
- Secrets are Railway variables only. Names are listed in `docs/DEPLOY.md`. Never print them.
- Staleness warning: `README.md` and the top of `docs/DEPLOY.md` still describe three services; the real set is in section 1.

## 8. Testing

Python is the virtualenv at `~/lonvera-tag-explorer/.venv/bin/python`. Tests need local Postgres 16.

```bash
cd ~/<your worktree>
export PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH
export PYTHONPATH=.
export TEST_API_DB=tagexp_apitest_$RANDOM        # unique per run so two runs never share a database
export UI_MOCK_PORT=$((5100 + RANDOM % 800))     # unique per run (default 5099 collides)
~/lonvera-tag-explorer/.venv/bin/python -m pytest tests -q
```

- The full suite takes 8 to 9 minutes. Run it in the background, ONE run at a time per machine.
- The tests create throwaway databases named from `TEST_API_DB` (also with `_alerts` and `_schema` suffixes). They skip, not fail, when Postgres is missing.
- One known clock-dependent failure: `tests/api/test_api_v19_extras.py::test_stale_combines_reasons_and_falls_back_to_etl_runs`. Judge it by whether it also fails on `build/v1`, not by the red mark alone.
- Gotchas:
  - `tests/etl/test_assignment_tags.py` and `tests/ui` cannot be listed in the same pytest command (stated by the owner, not re-tested here). Run them separately when selecting files.
  - `scripts/gen_schema_doc.py --check` needs a scratch database with every migration applied (`python -m etl.migrate`).
  - A new write route must be added to the allow-list in `tests/api/test_api_auth.py::test_the_api_is_read_only`.
  - A new table or column needs a description in `docs/schema/descriptions.py`, then regenerate the docs and (for a changed description after a migration was applied) put the comments in a NEW migration.
  - UI tests (`tests/ui`) need Playwright Chromium and use mock servers (`tests/ui/mock_*.py`), not the real database.

## 9. Safety rules every agent must follow

1. **The Creative Tracker and every Google Sheet are read-only for the app.** `etl/sheets_io.py` never writes. Do not add a sheet write.
2. **Never change Madre's Concept IDs.** Our IDs are vendor-prefixed (`Victoria-...`, `Internal-...`, `10x-...`); Madre keeps `Lonvera-...`.
3. **Meta writes only through `etl/meta_write.py`, only PAUSED, and only when launching is enabled** (`META_LAUNCH_ENABLED=1`, a listed user, dry run off). Everything else in the repo talks to Meta with GET calls only (`etl/meta_api.py`). Do not make Meta, Google or ExpandFi calls to "test" a doc or a change.
4. **Cancel or delete nothing in production data** without Josh's yes. Live data and live pipelines need a confirmed restatement first.
5. **No secrets** in the repo, in memory files or in chat. Never print a token, key or database URL. Point to the Railway variable or the owner's file instead.
6. **Never commit or push to `build/v1` or `main`.** Branch, pull request, merge.
7. **Applied migrations are immutable.** Add a new one.
8. **UI and doc text:** plain tables, no em dashes, expand every acronym on first use, no compliance hedging in ad copy or prompts (Josh owns compliance).
9. Never describe a number's cause as proven without a control (rates, not counts; say "consistent with" unless a randomized holdout exists).

## 10. Where do I look when...

| Situation | Look here |
|---|---|
| A number on a page looks wrong | `docs/CONTRACT.md` (metric rules), then `docs/UPSTACK_SOURCE.md` if the source switch is Upstack; check `/api/status` for data-through date; `app/queries.py` for the SQL; `/api/source/tieout` for the tie-out |
| A page is blank | Browser console, then `app/static/app.js` route table and the page file in `app/static/js/pages/`; check the matching `/api/...` route returns 200 with your login |
| The nightly job failed | `etl_runs` table (job, status, error), `docs/ALERTS.md` (what each Slack alert means), `scripts/deploy/run_etl.sh` (step order; later steps are non-fatal) |
| Data looks stale | `select * from analytics.data_coverage`; `etl_runs`; for feeds, `python -m etl.feeds status` and `docs/projection/MODULE_feeds.md` |
| Add a migration | Next free number above the highest on both `build/v1` and your base branch; additive and idempotent; then description in `docs/schema/descriptions.py` and `python scripts/gen_schema_doc.py --write` |
| Add an API route | `app/api.py` (or a blueprint); read-only routes need no test change; a write route needs the allow-list in `tests/api/test_api_auth.py::test_the_api_is_read_only` and a rate limit and Origin check like its neighbours |
| Add or change a tag line | The Communication Overview sheet is the source; `etl/load_taxonomy.py` loads it into `taxonomy_lines`; tags per ad in `ad_tags` (`etl/load_tags.py`, `etl/auto_tag.py`, `docs/AUTO_TAG.md`) |
| Change `/priorities` wording or rules | Wording and thresholds: constants at the top of `etl/creative_priorities.py` (the page prints them); layout: `app/static/js/pages/priorities.js`; spec: `docs/CREATIVE_PRIORITIES.md` |
| Change production board behavior | `app/priorities_assign.py` (stages, vendors, rules), `app/static/js/pages/priorities_production.js`, tests `tests/api/test_api_priorities_assign.py` and `tests/ui/test_priorities_production.py` |
| A vendor link stopped working | `creative_priorities_vendor_links`; reset with `POST /api/priorities/vendor-links/<vendor>/reset` (login required); regex `PUBLIC_API_RE` in `app/auth.py` |
| Give someone read access | SQL: `docs/AGENT_DATA_GUIDE.md` and the owner's URL file. HTTP: `python -m etl.api_keys create --name <who>` (API keys section of `docs/CREATIVE_PRIORITIES.md`) |
| Check or touch launching | `app/priorities_launch.py`, `app/priorities_launch_api.py`, `etl/launch_worker.py`, `etl/meta_write.py`; confirm `META_LAUNCH_ENABLED` is not `1` anywhere before assuming it is dark |
| Projection number looks wrong | `docs/projection/MODULE_<name>.md` and `QA_REPORT.md`, `app/projection/<module>/`, `/api/projection/audit` |
| Change a service schedule | `scripts/deploy/apply_*_service.py` (dry run first, `--apply` after review); cron minimum gap is 5 minutes |
| Database backup or restore | `docs/BACKUP.md` |
| Tests fail only on one run | Port or database name clash: set a fresh `UI_MOCK_PORT` and `TEST_API_DB`; see section 8 |
