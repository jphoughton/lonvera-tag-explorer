# REPORT_C (UI agent)

## Delivered (app/static/**, tests/ui/**)
Vanilla-JS SPA, no build step, Chart.js 4.4.1 vendored (`vendor/chart.umd.min.js`). Routes `/login`, `/`, `/p/<slug>`, `/ad/<id>` (History API, state in the URL). All data goes in via `textContent`; external URLs pass an http(s) check.
- **Header**: Back (pushState for drills, replaceState for every filter; restores expanded rows, sort, table + page scroll from history.state with sessionStorage fallback; with no in-app history it goes to the parent level; disabled on `/`), date presets + custom range, Stage chips (Testing / Scaling, Unclassified only when the window has it, at least one stays on, `stage=` include list), Compare to (Off / Previous period / 7d..90d / custom, Per day), range label line, clamped / `window_info.note` notices.
- **Product page**: dropdown tree (cluster > ICP > pain > family > Meta ad). Caret and (default "Drops down") row click expand in place, "Open >" navigates, "Opens" mode persisted in localStorage. Toolbar: Expand all, Collapse all, Expand to 0..3, Columns chips (Traffic on, Advanced off, persisted), search, With spend only. Lazy children from `/tree` (tag levels) and `/ads` (Meta ads under a family); sibling groups sort independently; total row pinned. Flat "All ad families" (`all=1`, `/families`), flat Meta ads table (`view=ads`), Campaigns view (stage cards, stacked daily chart, table), "Not on the tracker" panel, ad detail (media, tag block + badges + reliability note, Communication Overview, copy, siblings, family metrics, A|B|delta table, Meta ads, campaigns, daily chart with Spend/Revenue/Purchases/Impressions/Clicks toggles and A (blue) / B (orange) shading).
- **Columns everywhere**: Unique ads, Live now, Spend, % of spend, Impressions, Link clicks, CTR, CPC, [CPM, Conv rate], Purchases, Revenue, ROAS, CPA, Testing $/Scaling $/Testing % only when both stages are on. No 7d columns, no Active ads column. Compare adds a second line `vs B ▲12%` (CTR / Conv rate / % of spend in pp), colors per spec, sort by value or by % change (Delta button or shift-click).
- Copy follows v1.5: footer `Source: Meta Marketing API, refreshed <time>`; no "sheet" / "tab" / "ties to" wording; data-through comes from `/api/status`.

## Tests
- `tests/ui/mock_server.py` (Flask :5099) implements every contract endpoint through v1.5 (windows, stage list, compare, shares, traffic, `/tree`, unclassified days, impressions unknown before Jun 20). Login password `test`.
- `.venv/bin/python -m pytest tests/ui` : **64 passed** against the mock (about 3 min). Screenshots in `tests/ui/shots/`.
- `UI_BASE_URL=http://127.0.0.1:5098 UI_PASSWORD=test UI_ALLOW_RESOURCE_ERRORS=1 .venv/bin/python -m pytest tests/ui` against B's server on `tagexp_dev`: **all pass** (mock_only tests and campaign tests skip when that data is absent). Tests drill into whatever rows exist, so they are data-agnostic.

## Observations on the real API (for B / orchestrator)
1. `m.impressions` is `0` (not null) when unknown, `ctr/cpm` null. UI shows `–` when impressions = 0 next to spend or clicks. Prefer null.
2. `cpc` is `0.0` in totals when impressions are unknown (tree total, window=30d: spend 111,340, clicks 50,560, cpc 0.0). CPC should be spend / link clicks or null.
3. `by_stage` returns an `unclassified` card; UI renders one card per included stage (so 3 on current data). Campaign rows can sum to less than `total.spend` (days with no campaign), so shares sum below 100%.
4. Last refresh status `failed` is shown in the header (`refreshed ... (failed)`).
5. Assumptions to verify: `testing_pct` is a 0-1 fraction; `delta.spend_share_pp`, `ctr_pp`, `conv_rate_pp` are already in percentage points; CPM down = green, Conv rate up = green (contract only fixes CTR/CPC).
6. `/api/products` is a bare array: the UI derives the A / B range labels client-side. With `?envelope=1` it would use the server labels (not wired).

## Decisions / open items
- Active ads column dropped (not in Josh's list); one line in `columns.js` brings it back.
- Meta-ads tables label the share column "% of family" when filtered to one family, otherwise "% of spend". Ad-detail campaigns mini-table and the launch/7d/30d family table have no traffic columns (the API gives no traffic there).
- `tree?root=family` needs all three filters, so `all=1` uses the flat `/families` table.
- Static ES modules are not hashed: serve `/static/` with `Cache-Control: no-cache` (or version the URLs) so deploys are picked up.
- A first-time visit without a cookie logs two 401s in the console before the redirect to `/login` (expected).

## v1.6 charts and trend lines
- `app/static/js/trend.js` (panel, sparklines, pins), `charts.js` (`trendChart`), Trend + Chart columns in `columns.js`.
- Panel above the table on the products landing, every tag level, flat families, Meta ads, Campaigns view and the ad detail page (it replaces the old Daily chart there; full series from daily_start, window A blue / B orange shaded). Metric chips (CPM / Conv rate appear with the Advanced columns chip), Day | Week, Split by None | Stage | Top rows, Compare overlay (dashed gray, only while Compare is on), tooltip with date and every value, clickable legend, collapse (remembered in localStorage; a collapsed panel makes no /trend request). Stage areas are stacked for additive metrics, one line per stage for ratios (Testing teal, Scaling indigo; the badge / chip colors were swapped to match).
- URL state `tm` / `tg` / `ts` (carried through drills and Back). A metric change re-renders the page (tables re-request `spark=<metric>&spark_days=30`); grain and split only re-fetch the trend.
- "Trend" sparkline column (inline SVG 70x20, not sortable, no totals cell, Columns chip "Trend" default on) in tree rows (all levels incl. Meta ads), flat families, Meta ads, campaigns, products. "Chart" checkbox column pins a row (max 8, 9th disabled): one `/trend` request per pinned row with that row's scope; pins survive drill + Back.
- Landing page: no multi-product endpoint, so it requests `/trend?grain=day&split=stage` once per product and does week bucketing, sums, stage and top-rows on the client; its sparklines come from the same series.
- Mock: `/trend` (day/week, stage, children, cmp_points, partial weeks, launch note) and `spark` on rollup/tree/families/campaigns/ads/ad detail. Tests: 73 passed against the mock (9 new trend tests). Real API `/trend` was not ready (404) when I last probed, so the trend tests have not run against it yet: run `UI_BASE_URL=... pytest tests/ui -k "trend or spark or pin"` once B ships it.
- Gaps: unmatched table and the ad-detail campaigns table have no Trend / Chart columns (no spark in those endpoints); Meta-ad rows cannot be pinned (the trend scope has no ad filter).
