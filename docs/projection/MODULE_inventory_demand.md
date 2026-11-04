# Inventory demand (module `inventory_demand`)

What it replaced: the demand half of the inventory tabs: `Warehance Daily / Weekly / Fcst Inputs / Demand Fcst`, `Amazon Monthly Units / Daily Units / Fcst Inputs / Demand Fcst`, `Intl Daily Units / By Country / Fcst Inputs / Demand Fcst`, `Total Demand Fcst`. Output: units per month by location (`revamp` = US direct, `amazon`, `intl`) and product (`bull`, `liver`, `boneflex`, `belle`, `flow`), from loaded history through Dec 2029. `inventory_supply` consumes it.

## TLDR

- Engine ties to the sheet's Demand Fcst tabs exactly at 2026-11-03: 72 reconcile lines, 1,227 cells, max difference 0.00 (the sheet's own Amazon source seeded; see "Amazon source").
- Amazon units now come from `Amazon - Product Daily` (fresh through Nov 2), not `Amazon Daily Units` (stops Oct 9 after the two-day hold-back). That moves the Amazon baseline from 139.5 to 122.5 units a day (Vital Bull 43.8 to 31.4, Beef Liver 54.7 to 59.0, Boneflex 41.0 to 32.1) and FY27 total units by -1.3%. This is a data change, not an engine difference.
- International has not been refreshed since Oct 12 (22 days) and nothing in the sheet carries non-US shipments by day, so the real feed is shown stale, not patched.

## Files

```
migrations/140_proj_inventory_demand.sql           proj_units_daily, proj_units_monthly, proj_units_country, proj_units_loads, proj_demand_snapshot
app/projection/inventory_demand/__init__.py        MODULE (load_inputs, compute, blueprint, ui)
.../constants.py  levers.py  engine.py             locations and products, lever parsing and validation, the forecast (pure functions)
.../store.py                                       reads and writes of the tables (any psycopg connection)
.../table.py  routes.py  upload.py                 page payloads, HTTP layer, international CSV upload
.../reconcile_map.py                               72 lines against the Demand Fcst, Fcst Inputs and Total Demand Fcst tabs
etl/projection_inventory_demand.py                 read-only sheet loaders: seed, refresh, freshness hooks, CLI
app/static/js/projection/inventory_demand.js       the page
tests/projection/inventory_demand/                 151 tests
```

## Tables (migration 140, scenario independent)

| table | holds |
|---|---|
| `proj_units_daily(location, product, day, units, source, is_estimate, loaded_at)` | complete days only (the partial tail is never stored); `is_estimate` marks international days modeled from another feed |
| `proj_units_monthly(location, product, month, units, source)` | Amazon monthly history from the older monthly tab; the engine uses it for months a product's daily rows do not cover |
| `proj_units_country(month, country, units, days, source)` | international units by destination country (display only) |
| `proj_units_loads` | one row per load: source, rows, first and last day (drives nothing but the audit trail) |
| `proj_demand_snapshot(location, product, as_of, on_hand, allocated, source)` | US warehouse stock from `Warehance Fcst Inputs`, refreshed daily. `inventory_supply` keeps its own editable stock table; this one is the feed it may follow |

Levers are not a table: `proj_scenarios.params.inventory_demand`.

## Levers (per scenario, edited on the page or `PUT /inputs`)

```
inventory_demand: { as_of?: "YYYY-MM-DD",                       blank = today (Pacific); env PROJECTION_TODAY overrides for reconcile and tests
  locations: { revamp|amazon|intl: { baseline_days (28/30/30), lag_days (0), monthly_growth (0.0, a fraction),
                                     seasonality [12] (1.0), tie_to_dtc_revenue (false),
                                     products: { <slug>: { rate?, monthly_growth?, seasonality? } } } } }
```

Seeded on the default scenario from the sheet's Fcst Inputs tabs (baseline window, growth, seasonality), only when it holds none. `lag_days` means extra newest days to ignore; the sheet's built-in lag (Amazon 2 days, Warehance 1 day) is now the loaders' partial-tail rule, so the stored data already ends on a complete day.

## Engine rule

```
baseline        = units in the trailing baseline_days complete days / baseline_days         (divides by the window even if the data is shorter, as the sheet does)
rate(month)     = baseline x (1 + monthly_growth)^(months since the as-of month, never negative) x seasonality[calendar month]
units(month)    = actual units so far in the month the actuals end in + rate x days not yet covered      (later months: rate x days; earlier months: actuals only)
```

Earlier months use, per product: the daily sum when the product's daily history covers the whole month, else the monthly figure, else the partial daily sum. A feed that stops early (Amazon and international in the sheet) fills the gap days at the baseline, so nothing silently drops to zero. `tie_to_dtc_revenue` (off by default, US direct and international) scales forward units by projected new-customer plus repeat revenue per day relative to the same figure averaged over the baseline window; with no overlapping revenue it changes nothing and says so in `warnings`.

## Output for inventory_supply

`compute` returns `DemandDetail`, a subclass of `DemandResult` (so `isinstance` holds and the interface is unchanged):

- `units[(loc, prod)]`: full-month units, loc in `revamp|amazon|intl`, prod slug. Missing combinations are absent (`DemandResult.get` returns `{}`).
- `units[(loc + '_mtd', prod)] = {current month: actual units so far}`; remaining demand = `units[(loc, prod)][m0] - units[(loc + '_mtd', prod)][m0]` (the sheet's Demand Fcst row 14 minus row 13). Consumers iterating `units` skip locations ending `_mtd`.
- Working numbers for the page and reconcile: `rates`, `window_units`, `window`, `last_full_day`, `serial`, `mtd`, `daily_rate`, `location_total`, `product_total`, `total`, `share`, `estimate_days_in_window`, `warnings`.
- Current month `m0` = first of the month of `PROJECTION_TODAY`, else the scenario's `as_of`, else today (Pacific). The engine's `today` argument is not passed to modules, so reconcile must set the env var.

## API (`/api/projection/inventory-demand`)

| | |
|---|---|
| `GET /table?scenario=` | grid payload: all locations, by product, per location and product, units per day; shares; captions with each feed's last day |
| `GET /inputs?scenario=` | effective levers, per-product baselines, window, feed status (fresh, stale, estimated days), latest stock snapshot |
| `PUT /inputs` | `{scenario?, expected_version?, as_of?, locations:{loc:{...}}}`; validated (400 names the field), merged into the scenario params through the core (audit row, version bump, restorable) |
| `GET /daily?location=&product=&days=` | daily series with 7-day average and baseline overlay (7 to 400 days) |
| `GET /countries` | international units by country |
| `POST /upload-intl[?commit=1]` | multipart CSV of the overseas warehouse's shipments; preview returns a diff and a digest, commit needs that digest, closed days only, audited, estimates replaced |

Table rows use the shared grid's vocabulary (`calc|subtotal|total|header|ratio`, `num|pct`) directly; see QUESTIONS_inventory_demand.md.

## Daily refresh

`etl.projection_inventory_demand.refresh(conn, days=14)` (the hook `app/projection/freshness.py` and the daily runner call) reads, read-only through the service account (`GOOGLE_SA_JSON`, same as `etl/sheets_io.py`):

| feed | tab | held back (not stored) | stale after |
|---|---|---|---|
| US direct | `Warehance Daily` | current day (caption "is a partial day") | 2 days |
| Amazon | `Amazon - Product Daily` (fallback `Amazon Daily Units`) | today's row; two days in the fallback tab | 2 days |
| International | `Intl Daily Units` | none; days from the caption's estimate date are flagged `is_estimate` | 4 days |
| US stock | `Warehance Fcst Inputs` block "Current inventory" | n/a | n/a |

It upserts the trailing `days` complete days (rows the feed stopped reporting inside the window are removed unless another source such as an upload loaded them; a real count is never replaced by an estimate; unchanged rows are not touched), logs a load row and one `proj_audit` row, and commits. It reads and parses every tab before writing and rolls back on any error, so a bad tab leaves nothing half loaded. Empty or unreadable tabs raise `FeedError` with the tab name (no credentials). Returns `{"rows", "changed", "data_through" (the oldest feed's last complete day), "locations": {...held_back, estimate_days, source}, "stock", "warnings"}`; a fallback to `Amazon Daily Units` is listed in `warnings`. `freshness(conn)` returns one entry per location with the last complete day stored. Verified against the live sheet: second run changes 0 rows; data through Nov 2 (US direct), Nov 2 (Amazon), Oct 12 (international).

Commands: `python -m etl.projection_inventory_demand seed [--snapshot] [--amazon-source daily-units]` (history, monthly Amazon, country split, stock, levers; run once on an empty database), `refresh [--days 14]`, `status`.

## Amazon source: what changed and why

Compared over the days both tabs hold (live sheet, units of Vital Bull + Beef Liver + Boneflex):

| period | `Amazon Daily Units` | `Amazon - Product Daily` | difference |
|---|---|---|---|
| Jun 17 to Jun 30 | 2,689 | 2,689 | 0.00% |
| Jul | 5,986 | 5,985 | -0.02% |
| Aug | 5,160 | 5,135 | -0.48% |
| Sep | 4,246 | 4,114 | -3.11% |
| Oct 1 to 9 | 1,285 | 1,193 | -7.16% |

Product Daily against the monthly history tab, Sep 2024 to Aug 2026: 114,610 vs 114,584 units (-0.02%), no month over 2% where a product sold more than 200 units. Product Daily also carries Vital Belle and history back to Sep 24, 2024. So the two Amazon tabs agree where both are settled; Daily Units runs above Product Daily from September on (-3.1% in Sep, -7.2% in the first nine days of Oct), consistent with orders cancelled after the Oct 13 hand refresh. Product Daily is the daily-refreshed pull of the same ExpandFi table, so it is the source and Daily Units is only the fallback. Caveat: the newest weeks of Product Daily can still be revised down a few percent as orders cancel, so the Amazon baseline runs slightly high.

Effect on the forecast (engine, same levers): Amazon units per day 139.5 to 122.5; Dec 2026 Amazon 4,323 to 3,798; total Dec 2026 39,212 to 38,686; FY27 total 461,687 to 455,494 (-1.3%); FY27 Vital Bull on Amazon 15,975 to 11,461 (-28%), Boneflex 14,953 to 11,729 (-22%), Beef Liver 19,978 to 21,523 (+8%). The sheet's window (Sep 10 to Oct 9) is three weeks old; the latest 30 days run at 31 (Vital Bull) and 32 (Boneflex) units a day.

## International: not patched

No tab in the sheet carries non-US shipments by day: `Warehance Daily` is US shipments only (non-US orders are auto-cancelled in Warehance because Shopify routes them to the overseas warehouse), `Intl By Country` is monthly. The designed estimate (Warehance non-US orders x 0.89) was computed outside the sheet and its last day is Oct 12. The page and the freshness hook therefore show International as 22 days old, and 27 days of the 30-day baseline as estimated. The forecast from Oct 13 on is the baseline carried forward. Options: have `warehance_sync.py` append non-US orders x 0.89 to `Intl Daily Units` (then this loader picks it up unchanged), upload the monthly tracking CSV on the page, or give the app a Warehance key later (v1 never calls Warehance).

## Reconciliation (frozen snapshot, as of 2026-11-03, `PROJECTION_TODAY=2026-11-03`)

`scripts/projection/reconcile.py --module inventory_demand` needs the module's database to hold only the tables of built modules; the isolated run used the module's own database and a package holding just this module. Seeded with `seed --snapshot --amazon-source daily-units`:

| lines | cells | max abs diff | max rel diff |
|---|---|---|---|
| 63 lines: units, units per day, month to date, window units and baseline per product, last complete day, for revamp, amazon, intl (36 months from Nov 2026) | 903 | 0.00 | 0.0000% |
| 9 lines of Total Demand Fcst: 5 product totals, grand total, 3 location shares | 324 | 0.00 | 0.0000% |

Seeded the default way (Amazon from Product Daily) 22 lines differ, all Amazon or totals that include Amazon: Amazon units and per-day (bull -28.3%, boneflex -21.6%, liver +7.2% to +7.7%), month to date (Nov 1 and 2 now count), window units, baseline, the five Amazon-affected totals and the shares. Cause: the Amazon source above; nothing else differs.

Other differences from the sheet, all intended: (1) `TODAY()` becomes `as_of` / env; (2) months before the as-of month are reported (actuals from daily or monthly history; the sheet's forecast tabs start at the current month); (3) growth is anchored at the as-of month and never negative for gap months; (4) partial tail days are held back at load, not by a lag cell; (5) per-product daily coverage decides actual vs monthly history (a product that started selling later keeps its monthly history).

Sheet findings: `Amazon Daily Units` and `Intl Daily Units` are 3 weeks stale (per the feeds review, their refresher has only run as a dry run); the `Intl By Country` month headers are text like "Jul-26" that Sheets parsed as July 26 (read as a month here); `Amazon Fcst Inputs` baseline ends Oct 9 so its forecast ignores Oct 10 onward.

## Tests

`tests/projection/inventory_demand`: 151 tests: engine 23 (baseline, blend, growth, seasonality, lag, typed rate, stale feed gap, month-end and leap-year boundaries, history priority, per-product coverage, empty data, totals and zero shares, estimates, as-of resolution, revenue tie), levers 31, loaders and refresh 39 (idempotence, trailing-window replacement, removal rules, partial tail held back and reported, estimates never replace real counts, rollback on a bad tab, empty and unreadable tabs, fallback and its warning, freshness values and the core's `evaluate`), API 34 (401 with the app's login on, open with `AUTH_DISABLED`, payload shape and values, validation, audit row and version on every write, restore, clone isolation, 415 on a non-JSON body), upload 19 (parser layouts, preview writes nothing, digest required, estimates replaced, audit and load log, idempotent, upload rate limit), reconcile 5 (every map line exact on the snapshot, interface keys, Product Daily baseline from the snapshot rows). Run: `PYTHONPATH=. ~/lonvera-tag-explorer/.venv/bin/python -m pytest tests/projection/inventory_demand -q`.

Page checked in headless Chromium at 1440 and 390 px, light and dark: console clean, no sideways page scroll at 390, edit saved (version bump, grid and headline update), invalid input rejected inline, daily chart pickers, upload dialog preview and commit.

## Open items

- International real feed: stale since Oct 12 (see above); decide who writes the non-US estimate or uploads the tracking CSV.
- The sheet's Amazon Fcst Inputs should point at Product Daily so the sheet and the app agree; until then the Amazon lines differ by design.
- `tie_to_dtc_revenue` uses new-customer plus repeat revenue per day; the units-to-revenue relationship is proportional, which overstates swings when order value changes.
- Seasonality is the sheet's 12 repeating indices, all 1.00 (Josh's input); Amazon's reference column in the sheet suggests Nov and Dec run 1.13 to 1.26, not applied.
- Stock: the US warehouse snapshot lands in `proj_demand_snapshot`; Amazon and international stock are entered on `inventory_supply`.
