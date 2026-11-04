# Scenario modeler (page `/projection/modeler`)

What it is: a fast what-if workbench on top of the engine. Change monthly ad spend, average order value (AOV) and cost per new customer (CPA), pick the retention model, and watch yearly revenue and EBITDA move as you type. It replaces nothing in the sheet; it is the page Josh asked for ("adjust the monthly spend, AOV, CPA and which retention model to use, and it outputs yearly revenue and EBITDA"). It has no engine step: the three plan rows are the Spend plan page's own series and the retention model is the cohorts module's own levers, so this page and those pages always agree.

## Files

```
app/projection/modeler/__init__.py      MODULE (key modeler, ui modeler.js, blueprint at /api/projection/modeler); installs the draft overlay
app/projection/modeler/overlay.py       the read-only draft: a ContextVar wrapper on series.fetch_cells
app/projection/modeler/ops.py           typed-cell validation and the quick edits (fill, step, growth, ramp, percent, add, copy, reset); pure
app/projection/modeler/model.py         run_state(): draft run, grid payload, yearly results, retention, compare
app/projection/modeler/api.py           the endpoints below
app/static/js/projection/modeler.js     the page (styles are injected from the file)
tests/projection/modeler/               117 tests (own database tagexp_proj_modeler_test)
docs/projection/MODULE_modeler.md  QUESTIONS_modeler.md  TICKETS_modeler.md
```

One catalog line in `app/projection/interfaces.py` (`MODULE_CATALOG`): `"modeler": ("Scenario modeler", "P1")`, first in group P1, so the tab sits right after Overview (Pacing and Overview come first in the bar by the shell's own rule). No core test pinned the list.

## How a preview works (nothing is written)

`POST /api/projection/modeler/preview` runs the whole engine in memory on the draft. The draft is a set of cells for the three plan rows (and optionally a retention choice). `overlay.active(Draft)` wraps `series.fetch_cells` once; the wrapper is the identity unless a draft is active in the current context (a `ContextVar`, so another request never sees it). A draft cell with a value replaces the scenario's cell for that month; a cell with `null` takes the scenario's own cell away, so the month falls back to the base scenario's cell (a frozen copy has no fallback) exactly as the real `PUT /series` with `null` does. The retention draft is a copy of the scenario with `params.cohorts` merged. The engine then runs unchanged, so a preview equals the saved result (tested to the cent for spend, CPA and AOV, for a retention change and for an as-of curve with six cohorts and a ramp). The run uses the app's read-only connection, so a write would raise; `test_the_model_runs_on_a_read_only_connection` proves it, and `test_a_preview_writes_nothing` counts series rows, audit rows, history rows and the version before and after. A run takes about 30 ms (engine) and a preview round trip about 50 ms locally; a page test asserts the preview answers inside 300 ms.

Saving goes the normal audited way: grid edits are `PUT /series` (batched by the shell, one version per batch, audit row per cell), a retention choice is `PUT /cohorts/levers`. The page shows the preview at once and saves in the background (`Saving…`, then `Saved`). Closed months are locked: the grid cells are grey, a typed value is refused on the page and on the server (`400`, "Aug 2026 holds loaded figures from the books. Only planned months can change."), and a range edit skips them and says which.

## API (`/api/projection/modeler`, behind the app's login and the projection write guard)

| method and path | body / query | returns |
|---|---|---|
| `GET /state?scenario=` | | the page's state for the saved scenario: `scenario`, `plan_of_record`, `months`, `status`, `rows` (grid payload), `cellmeta`, `fy`, `captions`, `outputs`, `retention`, `levers`, `settings`; `por.outputs` when the scenario is not the plan of record |
| `POST /preview` | `{scenario, preview:true, edits?, op?, retention?}` | the same state on the draft, plus `applied:{text, row, count, skipped, edits}` when `op` is given. `preview:true` keeps the shell's plan-of-record guard from asking |
| `GET /compare?scenarios=a,b,c` | up to 5 slugs | each scenario's yearly table as saved |
| `POST /workspace` | `{}` | opens (or creates, as a frozen copy of the plan of record) the scenario named "Modeler"; `created:true` the first time |
| `POST /save-as` | `{source, name}` | a named, frozen copy of the working scenario (`201`); `409` for a duplicate name |
| `POST /reset` | `{scenario}` | puts the three plan rows and the retention model back to the plan of record's (audited per cell and as a params change, restorable from History); other settings stay; `400` on the plan of record |
| `PUT /settings` | `{scenario, ebitda_addbacks}` | which P&L lines sit below EBITDA |

`edits` = `[{line_key, month, value|null}]` for `drivers.spend_total`, `drivers.nc_aov`, `drivers.nc_cpa` only (at most 500). `retention` = `{curve_source: live|goal|asof, asof_month?, n_cohorts?}`, validated by the cohorts module's own parser and its as-of rule. `op` = `{type, row, from, to, ...}` with `type` fill (`value`), step (`value`, `then_value`, `step_month`), growth (`value`, `rate_pct`), ramp (`end_value`, optional `start_value`), percent (`pct`), add (`amount`), copy (`month`, `to`), reset. Values are validated with the Spend plan page's own limits (`drivers/validation.py`: spend 0 to 50,000,000; CPA and AOV above 0 and at most 1,000), so a zero CPA is a plain 400, never a divide.

## The page

Header with TLDR ("At $350K a month, $95 CPA and $69 AOV, FY27 revenue is $10.4M* and EBITDA $1.5M*."; "On average" when the plan moves inside the year), five KPIs (revenue and EBITDA with the difference to the plan of record, EBITDA margin, lowest cash balance with its month, repeat revenue per first order), a cue ("Working copy", "Modeling on: Modeler v5", and which curve repeat revenue uses). Then:

- **Plan inputs and what they produce.** Quick edits (row, change, from, through, value; the text under the form says what will change and the results panel previews it before Apply), then the grid: yellow rows ad spend (all channels), AOV, CPA; below them Meta and Google spend, new customers, new-customer revenue, return on ad spend, repeat revenue, Amazon and TikTok less the sales tax estimate, total revenue, EBITDA, EBITDA margin. Months are columns from January of the actual-through year; projected months are asterisked; carried cells blue; closed months grey. Type or paste a row from Sheets, Shift-click or Shift-arrow a range (the form follows the selection), Undo (also Ctrl+Z) goes back through edits and quick edits alike.
- **Retention model.** Current, Goal, As of month (month and 3 or 6 cohorts), each with lifetime and first-12-month multiples, the one in use marked. Choosing one previews it and saves it to this scenario only.
- **Compare.** The plan of record is pre-selected; up to 3 others; revenue and EBITDA by year with the difference to the scenario being edited (the first row follows what you type; others are as saved); Copy as table (tab-separated).
- **Results by year** (sticky on the right; on phones a sticky strip with the headline year and a Yearly results toggle): FY2026 (actual through Jul, projected after), FY2027, FY2028, FY2029 with total revenue, of which new-customer, repeat, and Amazon and TikTok less sales tax (so the parts add to the total), ad spend, EBITDA, EBITDA margin, the difference to the plan of record, lowest ending cash and its month, the EBITDA definition (with a select), a chart.
- **Safety.** The tab opens on a scenario named "Modeler" (created once as a copy of the plan of record, with a toast saying so). Arriving on the plan of record opens the copy; picking the plan of record on purpose from the bar is kept, and an edit there goes through the shell's plan-of-record guard (copy first). Save as scenario, Reset to plan of record (confirm; undo from History).

## Decisions

- **EBITDA.** The P&L module has no EBITDA line, so EBITDA = net operating income before the P&L's interest expense line (`pnl.fy[year].noi + pnl.fy[year].interest_expense`); the page says so in one line. The term-loan interest lives below it in Cash and debt. The P&L has no depreciation, amortization or tax lines, so nothing else is added back. Projected interest expense is $0 in the plan, so for projected years EBITDA equals net operating income; FY2026 adds back the $15,672 of actual interest. The Pacing page builds its own "EBITDA goal" (net operating income plus marketing influencers plus uncategorized expense, sheet cells `Automated Pacing!G31:G32`); the select "Same as Pacing" sets `modeler.ebitda_addbacks` to those three lines so the two pages can agree. Which one is the definition is Josh's call (QUESTIONS_modeler.md 1).
- **Partial FY2026.** Yearly figures are the P&L page's fiscal-year columns (`pnl.fy`), so FY2026 is the full calendar year: January to July from QuickBooks plus August to December projected, labelled "Actual through Jul" with an asterisk. The grid starts in January of that year. "Actual" is the P&L's boundary (books loaded); Aug to Oct 2026 are closed months without books and read as projected on this page.
- **Ad spend.** The input is the Spend plan's marketing spend (all channels). New customers = (Meta + Google share of it) / CPA, so $550K a month at $90 CPA buys about 5,780 customers a month, not 6,100. The row note and the "Spend on Meta and Google" row say so.
- **Which months are locked.** The Spend plan's own cell codes (`drivers.table.cell_codes`): spend is locked where books or the Meta feed hold it; CPA and AOV where a cohort row or a typed new-customer override already holds the month.
- **Drivers limits.** The shared `drivers/validation.py` limits are read through `ops.limits()`; no business number is written in this module (`PCT_LIMITS` of -99% to +1000% and the 5-scenario compare cap are technical limits).

## Variables (every knob; registry specs are requested in TICKETS_modeler.md)

| key | unit | default (plan of record) | where set | proving test |
|---|---|---|---|---|
| `drivers.spend_total` (monthly series) | dollars a month, all channels | $350,000 from Oct 2026 (Aug $847,183 and Sep $546,201 typed) | this page's grid and quick edits, the Spend plan page, core `PUT /series` | `test_the_preview_of_spend_cpa_and_aov_equals_what_saving_them_gives`, `test_an_edit_made_here_shows_on_the_spend_plan_page_and_the_other_way_round` |
| `drivers.nc_aov` (monthly series) | dollars per first order | $69 from Nov 2026 | same | same, `test_editing_spend_aov_and_cpa_cells_moves_the_outputs_and_saves` |
| `drivers.nc_cpa` (monthly series) | dollars per new customer | $95 from Nov 2026 | same | same, `test_a_zero_cpa_and_other_out_of_range_numbers_are_a_plain_400` |
| `cohorts.curve_source` | live / goal / asof | live | retention selector (`PUT /cohorts/levers`) | `test_the_preview_of_a_retention_choice_equals_saving_it`, `test_the_retention_selector_shows_each_option_and_writes_the_scenario` |
| `cohorts.asof_month` | first of a month | none | retention selector | `test_the_preview_of_an_as_of_curve_with_six_cohorts_and_a_ramp_equals_saving_them` |
| `cohorts.n_cohorts` | cohorts averaged (3 or 6) | 3 | retention selector | same |
| `modeler.ebitda_addbacks` | list of P&L lines | `["interest_expense"]` (choices: interest_expense, marketing_influencers, uncategorized) | EBITDA select (`PUT /modeler/settings`) | `test_the_ebitda_definition_is_a_setting_that_can_match_pacing`, `test_settings_are_audited_validated_and_scenario_specific`, `test_the_ebitda_definition_can_follow_pacing` |

Not variables: the headline year (the P&L's own `ev.year` setting, else the first fully projected year), the working scenario's name, the "On average" wording threshold (a plan that moves by more than 0.5% inside the year), the compact-number switch under a 470 px panel.

## Consistency (the acceptance test)

`test_modeler_consistency.py`: for the plan of record and three scenarios (changed spend, CPA and AOV; a changed retention model; an as-of curve with six cohorts and a changed spend and CPA) every year's total revenue, new-customer revenue, repeat revenue, ad spend and net operating income equals the P&L page's fiscal-year value (`GET /pnl/table` `fy`) within $0.005, EBITDA equals net operating income plus interest expense, yearly revenue and net operating income equal `GET /run`'s headline, and the lowest cash and its month equal `/run`'s `lowest_ending_cash`. A preview ties to the P&L page once saved. Every input cell equals the Spend plan page's cell for the same scenario; an instruction here writes the same cells as the Spend plan page's pattern.

Numbers on the seeded plan of record (snapshot 2026-11-03): FY2026 revenue $18,919,266 / EBITDA $2,709,481, FY2027 $10,379,147 / $1,526,273, FY2028 $9,670,857 / $1,057,565, FY2029 $9,416,524 / $889,261; lowest cash -$1,173,778 in Dec 2028. With $550,000 a month from Nov 2026 at the same CPA and AOV: FY2027 revenue $13,292,284, EBITDA $1,054,030.

## What depends on the cohorts feature

The retention selector uses `GET /cohorts/curve-options?n=` (which months can be built), `GET /cohorts/curve-compare?n=&asof=` (lifetime and first-12-month multiples for Current, Goal, As of), `PUT /cohorts/levers` (`curve_source`, `asof_month`, `n_cohorts`) and `cohorts.compare.asof_problem` / `cohorts.engine.parse_levers` for validation in the preview. The preview also reads `CohortResultX.curve_label`, `lifetime_multiple`, `twelve_month_multiple`. If those names change, `test_modeler_model.py` (retention) and the retention page test fail. Without the cohorts feature the page still works: the card says the options could not load and the model keeps the saved choice.

## Open items

See `QUESTIONS_modeler.md` (EBITDA definition, ad spend scope) and `TICKETS_modeler.md` (rate limit for previews, a grid lock and public edit hook, the registry specs, the Overview purpose line).
