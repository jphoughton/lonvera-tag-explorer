# Projection QA report (independent pass)

Run 2026-11-03 evening Pacific (2026-11-04 05:30-06:30 UTC). Code under test: `feat/projection` HEAD `1a4422c` for LIVE (deployed behaviour inferred from the live API) and the dirty worktree (78 files changed while I worked) for LOCAL. LIVE was touched with GET/HEAD/OPTIONS only. LOCAL = `tagexp_proj_qa`, a copy of `tagexp_proj_integration`, app on :5294. Evidence screenshots: `/private/tmp/claude-501/-Users-joshhoughton-Desktop-Google-Sheets/932f404b-d705-460f-8d9b-9b06f5b4c739/scratchpad/qa-shots/` (named below). Scripts: `.../scratchpad/qa/` and `tests/projection/qa/probe_stub_fallback.py`.

## Context (not defects)

- The live `current` scenario is Josh's own edited plan (his audit rows: spend_total / nc_cpa changes, deleted `principal_paid` cells, a tax override; version went 2 -> 120+ during the pass, 5 named copies). Headline numbers on live moved while I tested (lowest ending cash was -$1.215M at v2, -$353K at v11, -$343K at v52). I judged live only for internal agreement, using atomic snapshots (version read before and after every batch). Sheet-baseline comparisons use the LOCAL frozen copy.
- The worktree was being edited by other agents (engine degrade logic, grid vocabulary, cohorts picker, pacing levers, variables module, feeds). Some local results below changed between runs; each is marked where that matters.

## Counts

| severity | count |
|---|---|
| BLOCKER | 0 |
| HIGH | 4 |
| MEDIUM | 10 |
| LOW | 11 |

No BLOCKER: nothing makes the model wrong on the happy path; every HIGH is a way a plausible mistake (bad upload, one failing module, a mis-click) silently produces wrong numbers that cannot be reverted from the UI.

## HIGH

### H1. One failing module silently becomes zeros or a snapshot stub; the UI never reads the module status
- Where: `app/projection/engine.py` (`run`, committed version substitutes `stubs.stub_for`), `GET /api/projection/run` (`modules.<key>.stub/error`), `app/static/js/projection/shell.js` (headline strip), all dependent pages.
- Evidence (probe, GET only, module `pnl` forced to raise, snapshot dir absent = production): `/run` headline `fy.2027 = {revenue: 0.0, noi: 0.0}`, `lowest_ending_cash -7,818,439`; `/pnl/*` 500, but `/financing/table` 200 with 296 non-zero cells and no warning, `/pacing/month` 200 with every goal 0 and no warning. With the snapshot present (development) the same failure shows the SHEET's FY27 $10,340,541 / $1,501,512 as if they were the model's. `grep stub app/static/js/projection/*.js` finds only `inventory_supply.js:166` (`demand_stub`); nothing reads `/run.modules`. Strip shows "FY27 revenue $0, FY27 NOI $0, Lowest ending cash -$7.8M" (not hidden, because two items are non-zero).
- The uncommitted engine patch (degraded modules return None, `/run.degraded`) fixes the stub but not the propagation: with `pnl` raising, `/run` gives `fy: null` yet `lowest_ending_cash -7,818,439`, `/financing/table` still 200 on zero NOI, `/pacing/month` still 200 with zero goals.
- Expected: any degraded upstream marks every dependent page (banner + 503/`degraded` payload) and hides the headline; never zeros that look like data.
- Severity: Josh reads these numbers to decide on a $7.5M deal; a silent fallback to zero cash flow is the worst failure mode this app has.
- Repro: `DATABASE_URL=postgresql://localhost/tagexp_proj_qa SECRET_KEY=x PYTHONPATH=. PROJECTION_SNAPSHOT_DIR=/nonexistent python tests/projection/qa/probe_stub_fallback.py pnl`.

### H2. QuickBooks upload: a partial file commits, flips months to "actual" with no costs, and cannot be undone
- Where: `POST /api/projection/pnl/upload` (`app/projection/pnl/api.py:338`, `qb.py`), History drawer.
- Evidence (LOCAL): file with only `Amazon Revenue` and `Amazon Ads` for Aug-Oct 2026. Preview: `committable: true`, `warnings/notes: []` (unmapped `Shopify Sales` $2.41M is listed but not blocking). Commit 200. P&L Sep-26 before: status projected, NOI $227,738; after: status actual, NOI $1,254,631 (every expense line becomes "actual" with no rows = 0); P&L actual_through Jul -> Oct. `scenarios/current` version stayed 2 (history `[(2,params),(1,baseline)]`), so History cannot restore; only an `audit` row (`pnl.upload`) exists, and its `old.changed` is truncated to 300 cells. No delete endpoint for loaded actuals.
- Expected: preview shows before/after NOI per month and blocks (or demands a typed confirmation) when mapped totals are far below the existing month or expected lines (COGS, shipping, payroll) are absent; each commit is a restorable version.
- Severity: one wrong export silently inflates NOI 5x for the months the model treats as truth, with no way back from the UI.

### H3. Cohort upload accepts garbage and has no undo; a committed bad file turns the repeat curve negative
- Where: `POST /api/projection/cohorts/upload` (`app/projection/cohorts/api.py:127`, `upload.py`).
- Evidence (LOCAL): long-format file rows `2026-10,0,12,99999` and `2026-09,0,5641,-5`. Preview `warnings: []`, shows first-order $99,999 (+146,271%) and -$5, new customers 3,614 -> 12 (-99.7%). Commit 200. `lifetime_multiple` 1.579 -> **-2.360**; FY27 NOI $1.526M -> $1.448M; `current` version unchanged; no restore path.
- Expected: reject or require confirmation for negative $, per-customer first order outside a sane band, or >50% revision of a closed cohort; commits restorable.
- Severity: same as H2; cohorts drive repeat revenue (60% of FY27 revenue).

### H4. Plan-of-record supply edits (POs, transfers, stock counts) are not versioned, so History/Restore does not undo them
- Where: `app/projection/inventory_supply/api.py` write section ("For the plan of record ... it edits the stored rows"), `POST/PATCH/DELETE /po`, `/transfers`, `/stock`, `/plan/accept`.
- Evidence (LOCAL): `current` at version 4. `POST /inventory-supply/po` (30,000 Vital Bull) -> 201, version still 4, audit row `inventory_supply.po.create`; Dec-26 cash 244,691 -> 169,691. `POST /scenarios/current/restore {version:4}` -> 200, version 4 -> 5; the PO is still there (`PO still there? True`), cash unchanged. In a copy scenario the same edit is an overlay, versioned and restores exactly (verified: PO 16 delayed 9 months moved Vital Bull Revamp stock-out 2029-04-17 -> 2027-03-18; restore returned the original burn to the unit).
- Expected: contract says "every mutation ... restorable"; the History drawer shows nothing for PO edits on the plan of record.
- Severity: POs are the largest cash lever in the model and the one place the undo promise silently does not hold.

## MEDIUM

### M1. Spend grid and `PUT /series` accept negative, absurd and inert values
- Where: `PUT /api/projection/series` (core), Spend plan grid.
- Evidence (LOCAL): typing `-350k` or `(500000)` into Mar-27 spend saves -350,000 / -500,000 (version bumps, no warning; NOI Mar-27 moves up); `500000350000` (a stray paste) saved: NOI Mar-27 = -$300.2 billion. `9.9e12` accepted (FY27 revenue $12.7 trillion). The pattern endpoint bounds the same cell 0..50,000,000 (`/drivers/patterns` -> 400 "must stay between 0.00 and 50,000,000"), the grid does not. Also accepted with 200 and a version bump: a typed value on a closed month (May-26 777,000 is ignored by the engine, May-26 still 1,344,923), `pnl.noi`, `zzz.unknown`, 500 junk `drivers.k0..k499` keys. Screenshot `local_drivers_edit_dark.png`.
- Expected: per-line bounds and "month is actual" rejection in one place (core validators registered by modules).

### M2. `PATCH /scenarios/<slug>` bypasses every module validator
- Evidence (LOCAL): `{"params":{"financing":{"tax":{"rate":7}}}}` -> 200 (the module endpoint rejects it: "must be at most 1"); lowest ending cash -$1.17M -> **-$21.3M**. `debt.principal -1e9`, `drivers.sales_tax_rate 5` (silently ignored, stored), `payback.horizon_months "x"` all stored. The Financing page then echoes `rate: 7` as the effective input.
- Expected: PATCH accepts `name` only, or routes params through the owning module's validator.

### M3. The UI never sends `expected_version`: two tabs overwrite each other silently
- Evidence (LOCAL, two tabs on `qa-ui` at v9): tab A saves Apr-27 = 111,111 (v10); stale tab B saves the same cell = 222,222 and succeeds (v11), no toast (`local_stale_tab_dark.png`). The API supports 409 on `expected_version` (verified: two clients with the same version -> 200 and 409), `grep expected_version app/static/js/projection` has no hits.
- Delete-in-another-tab is handled well: toast "Not saved: scenario not found", reload falls back to `current`.

### M4. Direct edit of the plan of record has no friction (relayed finding, reproduced)
- Evidence (LOCAL): `/projection/drivers?scenario=current`, double-click Jul-27 spend, type 360000, Enter: saved at once, no dialog/confirm, body `{"scenario":"current","items":[...]}` with no `expected_version`, version 2 -> 3. The bar shows a "Plan of record" chip only. Pacing goals, the headline and Cash & debt read this scenario.
- Expected: warn or confirm on first edit per session in the default scenario, or default new sessions to a copy.

### M5. Different pages disagree about which months are actual, and about Aug-Oct 2026 values
- Evidence (LIVE v11/v52 atomic snapshots): `/pnl/table` status = 19 actual months (through Jul-26), Spend plan / Cohorts / Cash & debt / Demand = 22 (through Oct-26). The P&L page shows the strip "Actual through Oct 2026" next to "QuickBooks loaded through Jul 2026; Aug-Oct are projected" (`live_pnl_actual_through_dark.png`).
- Repeat revenue: Jul-26 Cohorts 885,989 vs P&L 944,174; Aug-26 Cohorts 795,336 vs P&L 984,287 (typed; cohort-export actual 839,643, so the P&L figure is 17% above the data). New-customer revenue Aug/Sep/Oct-26: P&L 515,139 / 401,590 / 240,355 vs cohort table 534,185 / 400,643 / 246,904 (3.7%, -0.2%, 2.7%). `SHEET_FINDINGS.md` #19 says "Cohort table drives"; the typed sheet values drive.
- Everything from Nov-26 on agrees exactly (checked per month).

### M6. Two clocks: `date.today()` (server UTC) vs Pacific across modules
- Evidence (LIVE, 06:10 UTC = 22:10 Pacific): `/inventory-supply/summary` `today: 2026-11-04`; `/inventory-demand/table` `as_of: 2026-11-03`; `/pacing/month` `as_of: 2026-11-03`. Code: Pacific in `freshness.py`, `inventory_demand/engine.py`, `pacing/periods.py`; `date.today()` in `engine.py:136`, `inventory_supply/loaders.py:22`, `inventory_supply/freshness.py:27`, `pnl/store.py:75`, `pnl/model.py:274`, `pnl/views.py:164`.
- Effect: every evening from 16:00 to 24:00 Pacific, supply and the P&L's closed-month boundary are one day ahead of pacing and demand; on the last day of a month the P&L treats the month as closed 8 hours early.

### M7. Pacing "Meta spend today" card is dead on live
- Evidence: `GET /pacing/intraday` -> `{"loaded": false, "message": "The hourly spend curve is not loaded."}`; entering $12,000 in the card shows that sentence (`live_pacing_intraday_dark.png`). `proj_pacing_hourly` is empty on production; `seed-hourly` (MODULE_pacing.md) was never run there and `/freshness` does not flag it.

### M8. `pytest tests/projection` as one run fails: 96 failed, 1844 passed, 6 skipped (91 s)
- Cause: every module conftest has a session-scoped autouse fixture (`only_this_module`, e.g. `tests/projection/inventory_demand/conftest.py:38`) that monkeypatches `registry.PACKAGE` and clears `registry._cache` and only undoes it at session end. Once inventory_demand's fixture has run, later suites build apps that register only the earlier suite's module: `GET /api/projection/inventory-supply/po` returns `{"error":"not_found"}` -> 75 inventory_supply failures (`KeyError 'pos'`, `404 == 400`). Pairwise: `inventory_demand + inventory_supply` 76 failed; `core + supply` 2; `pnl + supply` 6 (1 m 55 s); `drivers + supply` 1; `cohorts/financing/pacing/design/snapshot + supply` clean; `ui + supply` collection error. The remaining single-run failures (core_grid_vocab 13, core_restore_safety 5, ui 1, variables 2) came from files other agents were editing at that moment.
- Per-directory runs pass (counts below), but there is no CI-safe way to run everything together.

### M9. The public URL is open for writes: guard rails are weaker than they look (decision, not bug)
- Evidence (LOCAL guard-rail battery): Origin mismatch / `null` -> 403, text/plain -> 415, 1.2 MB -> 413, `current` delete -> 409, delete without matching `confirm` -> 400, 120 mutations/min -> 429 `Retry-After: 40`, uploads 20/hour -> 429 at #19 (previews count). BUT a request with no `Origin` header (curl, any script) is accepted (`PUT /series` -> 200), the delete `confirm` is the scenario slug (public in `/scenarios`), scenario delete is not restorable, and uploads (H2, H3) cannot be undone. Anyone who learns the URL can delete Josh's five named copies or poison actuals within the rate limit.

### M10. Tag Explorer pages overflow horizontally at 390 px
- Evidence (LIVE): `/`, `/p/vita-bull`, `/p/beef-liver`, `/login` all have `scrollWidth 445` at a 390 viewport; the top-bar text ("Data through Nov 2 · refreshed ...") is clipped at the right edge (`tags__390_dark.png`). Projection pages: no overflow at any width. Dark emulation of the Tags pages is correct: they stay light and readable (`tags_p_vita-bull_1440_dark.png`).

## LOW

- L1. Projected-month asterisk (`span.pg-ast`, `span.pacing-ast`) fails contrast on LIVE: dark rgb(139,132,123) on rgb(30,33,38) 4.41:1 (631 per page), 4.17:1 on the amber input cells; light 3.48:1 (631) and 3.14-3.96 on tinted cells. It carries the "projected" meaning. The uncommitted tree already has 0 failures in my local sweep. All other low-ratio text found was on disabled buttons (exempt).
- L2. Literal text "null" renders above the PO table on `/projection/inventory-supply` (live): `inventory_supply.js:364` passes `isPlan ? null : h(...)` and `poStatusLine` straight to `replaceChildren`, which stringifies null.
- L3. Nov-26 demand differs between pages by design but both say "Demand": Demand page 6,996 Vital Bull at Revamp (units so far + forecast); Supply burn 6,292 (remaining after the 11-03 stock snapshot); difference 704 = shipped Nov 1-2. Same for Amazon, total. Needs a row note.
- L4. `as_of` is a public query parameter on `/pacing/*` (e.g. `as_of=2035-01-01` returns month 2034-12); `/pacing/rollup?from=2026-11-01&to=2020-01-01` returns 200; `bands=zzz` / `view=zzz` on catalog return empty 200.
- L5. P&L cost drivers accept a rate of 500% or -20% (`PUT /pnl/drivers` amazon_fba_fees); `PUT /financing/debt` accepts principal for a month before the close (inert) and `cash.floor -1e9`.
- L6. Scenario slugs lose non-ASCII (`ünïcode ✓` -> `n-code`); a scenario named "current" gets slug `current-2`. XSS is clean: `<img onerror>` and `<b>` names render as text (`local_xss_scenario_dark.png`).
- L7. `proj_units_loads` grows 3 rows per refresh run; `proj_audit` and `proj_scenario_history` have no pruning beyond 200 versions.
- L8. Refresh runner reports `UNKNOWN ... has no data yet` for daily feeds `drivers.meta_spend` and `pacing/meta_ad_daily` without failing (by design). `inventory_demand.intl` is labelled `monthly-upload` with `stale_after_days 4` (22 days old, never fatal).
- L9. Doc drift: SHEET_FINDINGS #15 says the app reports FY26 NOI $2.67M; local app $2.69M, live $2.58M. #2 says lowest cash -$1.226M; local app -$1.174M (G3 below). SCOPE.md "NOI $0.91M (FY26)" is the sheet's Sep-Dec figure.
- L10. `/inventory-supply/*` grids use a 38-month axis (Nov-26..Dec-29) not the contract's 60 (documented in MODULE_inventory_supply.md); pacing payloads have their own axes. Shell handles both.
- L11. Focus: four date/time inputs showed no outline in my Tab walk (pacing `Time of day, Pacific`, supply `order_date`/`land_date`, financing `2027-09-01`); programmatic focus on the same elements does show a 2 px outline, so I could not confirm it. Skip-link wrap and tab order otherwise follow reading order on all 8 pages.

## G. Five headline numbers re-derived from the snapshot (own Python on `~/lonvera-tag-explorer-data/projection-snapshot-2026-11-03`, no engine import)

| number | snapshot (sheet cells) | app (LOCAL frozen) | diff | read |
|---|---|---|---|---|
| FY27 total revenue (P&L row 22, Jan-Dec 2027) | 10,340,540.51 | 10,379,146.77 | +38,606 (+0.37%) | inside 0.5%; cause = SHEET_FINDINGS #4 |
| FY27 net operating income (row 100) | 1,501,511.78 | 1,526,272.96 | +24,761 (+1.65%) | above 0.5%; documented (#4, cohort table vs stale typed NC revenue) |
| Lowest ending cash (row 131, Dec-28) | -1,097,691.73 | -1,173,777.78 | -76,086 (-6.9%) | direction matches #2 (tax -127,855) offset by higher NOI; the doc's -$1.226M is not reproduced |
| Pacing revenue goal, Nov-26 | 1,025,894.29 | 1,034,624.65 | +8,731 (+0.85%) | repeat +9,387 (684,038 vs 674,650), NC revenue goal identical (240,289.78) |
| Vital Bull months of cover at Revamp, Nov-26 close | 4.5335 | 4.533 | < 0.001 | exact |

## Section A (API sweep, LIVE, 70 GETs)

- Every GET returned 200; max 0.38 s, mean 0.13 s. No NaN/Infinity tokens, no strings in numeric rows, every table payload has `months/status/rows/captions` except the pacing payloads, which use their own shapes (`months` is the month picker, no `status`; `/pacing/snapshot` has no `captions`). All table rows have `values` of the axis length. `X-Robots-Tag: noindex, nofollow` on every response; `/robots.txt` = `Disallow: /`; no CORS headers; `OPTIONS` preflight returns 200 with no `Access-Control-*`.
- Param fuzz (60 bad values: dates, negatives, `1e999`, 2000 keys, 50-scenario compares): 0 x 5xx.
- `/run?scenario=current`: all seven modules `built:true, stub:false` on live; nothing is falling back today (H1 is about what happens when one does).
- Live `/freshness`: 17 fresh, 5 stale, 0 unknown (QuickBooks and books 95 days, intl demand 22, FBA and intl stock 21). The stale entries are accurate.

## Section B (cross-page agreement, LIVE atomic snapshots, v11)

Agree exactly (every month, tolerance $0.01): FY27 revenue $10,379,146.77 and NOI $1,526,272.96 on `/run`, `/pnl/table` FY, `/pnl/summary`, `/pnl/ev` basis, `/drivers/table` summary; implied value 3x-8x identical on `/run`, `/pnl/ev`, `/pnl/summary`; spend plan `spend_total` = P&L `marketing_spend` and channel lines (Meta/Google/Amazon/TikTok/Bing); P&L revenue lines = Spend plan revenue lines (60 months); cohorts `repeat_rev` = P&L repeat except Jul/Aug-26 (M5); cohort `first_order_rev` = Spend plan NC revenue from Nov-26 (M5 for Aug-Oct); inventory outlay total = Cash & debt inventory buys (every month from close); Cash & debt NOI before inventory = P&L NOI + China COGS (design, `financing/model.py`) except Sep-26 (typed $150,000 override); pacing Nov-26 goals = P&L for revenue, DTC, NC, repeat, Amazon, sales tax, COGS, NOI, Meta, Google, Amazon ads (11 lines); sales tax 7.0% of DTC on Spend plan, P&L, pacing MTD; payback: Cash & debt 10 months (AOV 69.156, CAC 105.49) = pacing month-to-date 10 (CPA 105.4995); Spend plan 6 and pacing goal 5 months are plan economics on the live vs goal curve (different curve, not a bug but three numbers for "payback"); lifetime multiple 1.5788 on `/run` and Cohorts. Demand vs supply burn demand: 646 cells compared, all equal except the current month (L3). Disagreements: M5, M6, L3.

## Section C (browser, LIVE and LOCAL, headless Chromium, 8 pages x 4 widths x dark and light = 64 loads each, plus Tags pages)

- Console errors 0, failed requests 0, horizontal page overflow 0, controls without a name 0, on every projection page at 1440/1024/768/390 in both themes (LIVE). LOCAL (mid-edit tree, 72 loads incl. `/projection/variables`): same, except one transient overflow on `/projection/cohorts` (`table.co-compare right=1485` at 1440) that did not reproduce 10 minutes later.
- Contrast: only L1 (and disabled buttons). Dark first: dark body `rgb(22,19,16)` on all pages; screenshots `live_pacing_1440_dark.png`, `live_financing_1440_dark.png`, `live_inventory-supply_390_dark.png` read well.
- `/` and `/p/<product>` under dark emulation render in the forced light theme, readable (M10 for mobile width).

## Section D (writes, LOCAL)

Verified working: spend-plan cell edit (UI) -> audit row, version +1, P&L marketing = 500,000, NOI Mar-27 144,440 -> 57,817, cash Dec-27 -243,637 for a 150k/month pattern fill, repeat revenue Mar-28 +69,632, restore returns every compared figure exactly; pattern preview writes nothing; closed-month pattern -> 400 "hold loaded actuals"; History drawer lists versions with plain-language diffs, UI Restore round-trips; concurrent edits: stale `expected_version` -> 409, 6 identical concurrent PUTs -> 1 change, 10 concurrent distinct cells -> 10 stored, versions sequential; rapid scenario switch while saving wrote only the original scenario; back/forward keeps the scenario; reload mid-edit discards the unsaved text; deleted-in-other-tab handled (M3); COGS price lever (SHEET_FINDINGS #1): -$440,741 FY27 NOI, pacing NOI goal and COGS goal follow; PO add/delay/delete moves outlay, financing buys, Dec-26 cash and reorder stock-out dates; PO validation (negative/huge units, bad product/warehouse/status/date, land before order, extra fields) all 400; P&L settings validation (8 bad values) all 400; pacing levers (-1, 400, "x", unknown) 400; financing debt (negative principal, 600 rows, rate 5, duplicate month) 400; scenario name rules (empty, 500 chars, duplicate, control chars) 400/409.
Not verified clean: H2, H3, H4, M1-M3 above.

## Section E (daily refresh, LOCAL)

- Runs 1-3 (`--days 14`): exit 0; `inventory_demand` 154 rows (7 s), `pacing` 272 rows (5.5 s), `drivers`/`inventory_supply` 0 rows. Idempotent: all `proj_feed_*` and `proj_units_*` tables identical in row count and content hash across runs (timestamps excluded); only `proj_units_loads` (a log) grows by 3 rows per run (L7).
- Forced failure (`GOOGLE_SA_JSON` set to an invalid string for that command): exit 1, message `FAILED: inventory_demand refresh failed; inventory_supply refresh failed; pacing refresh failed`, other modules continued, `proj_refresh_runs` rows `failed` with scrubbed errors (the dummy secret string appears 0 times in the output).
- `/api/projection/freshness` matches the runner exactly (same feeds, statuses, `last_run` failed for the three modules); note the data still reads `fresh` after a failed run until it ages past `stale_after_days`.

## Section F (tests, per directory, first pass 21:45-22:10 Pacific)

| suite | result |
|---|---|
| core | 193 passed (later 222 after core edits) |
| snapshot | 21 passed |
| cohorts | 102 passed |
| drivers | 171 passed |
| pnl | 138 passed |
| inventory_demand | 151 passed |
| inventory_supply | 183 passed |
| financing | 171 passed |
| pacing | 130 passed (later 2 failures in `test_pacing_page.py` while pacing levers were being edited) |
| design | 55 passed (later 60) |
| ui | 94 passed (later 2 failed: `test_mobile_390_no_page_overflow_table_scrolls_inside`, `test_bar_is_one_row_at_every_width[390]`, after `shell.css` edits) |
| variables (uncommitted, outside the list) | 454 passed, 2 failed (`pacing.cost_warn`, `pacing.below_ebitda_rows` round trip) |
| all in one run | 96 failed, 1844 passed, 6 skipped: see M8 |

## What I could not test

- Writes on LIVE (read-only rule), so H2/H3/M1 are proven locally on a copy of the integration DB, not on the production database. Production values of `PROJECTION_TODAY`, `GOOGLE_SA_JSON`, `AUTH_DISABLED` and the hourly-curve seed are unknown to me (M6 and M7 are observed from outside).
- Real QuickBooks and cohort exports (I used synthetic files); `reconcile.py --live` against the live sheet (I compared against the frozen snapshot only); Railway cron failure notifications; Python 3.12 (ran 3.14); Safari/Firefox; screen readers; load under concurrent users; a second real browser profile for the two-tab case (two tabs of one context).
