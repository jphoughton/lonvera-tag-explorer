# Projections: design and usability review (2026-11-03)

Scope: every Projection page (Pacing, Overview, Cohorts, Spend plan, P&L, Inventory demand, Inventory supply, Financing, Variables) in dark first, then light, at 1440, 1024, 768 and 390px, against a local copy. Method: `tests/projection/design/tools/vet.py` (WCAG contrast of every text node and control against its composited background, type size, clipping, control edges, charts, overflow, console), `vet_states.py` (menus, History, dialogs, cell editor), `vet_touch.py`, `vet_tasks.py` (seven walkthroughs, timed), then eyes on screenshots. Screenshots: `/private/tmp/claude-501/-Users-joshhoughton-Desktop-Google-Sheets/932f404b-d705-460f-8d9b-9b06f5b4c739/scratchpad/design-vet/` (called `$V` below). Nothing here was run against the live site.

## TLDR

- The pages were close to the brand but not readable: 72 page loads produced 12,376 text nodes under 12px, 547 contrast failures, 300 controls with an edge under 3:1, and Variables scrolled sideways on a phone. After the fixes the same 72 loads produce 0, 0, 0 and 0 (`$V/final2/audit.json`), and every menu, drawer, dialog and the cell editor is clean in both themes (`$V/states-final`).
- The biggest risk was not visual. Josh edited the plan of record 119 times in 30 minutes with nothing in the way. The plan of record is now labelled everywhere, the first edit per tab session asks "Make a copy and edit it", History restores the session start in one click, stale tabs cannot overwrite each other, and a broken source shows one plain banner instead of zeros.
- Seven walkthroughs finish in 1.2 to 6.1 seconds on the happy path. Two real design gaps remain in module pages (a one-click overwrite of the Goal curve, a bulk fill with no Undo) and are ticketed.

## Scores (1 to 5, 5 best)

| Page | Clarity | Speed | Error-proneness | Density | Consistency | Verdict |
|---|---|---|---|---|---|---|
| Pacing | 4 | 5 | 5 | 4 | 5 | Ready with caveats |
| Overview | 4 | 4 | 5 | 4 | 5 | Ready |
| Cohorts | 4 | 4 | 3 | 4 | 4 | Ready with caveats |
| Spend plan (Drivers) | 4 | 5 | 3 | 4 | 4 | Ready with caveats |
| P&L | 4 | 4 | 4 | 3 | 5 | Ready |
| Inventory demand | 4 | 4 | 4 | 4 | 5 | Ready |
| Inventory supply | 3 | 3 | 4 | 3 | 4 | Ready with caveats |
| Financing | 4 | 4 | 4 | 4 | 5 | Ready |
| Variables | 3 | 3 | 4 | 3 | 4 | Not ready (still being built by its owner) |

Scoring: clarity = can a first-time reader say what the page says in one read; speed = actions and seconds to the main task; error-proneness = how easily a wrong value or a destructive click happens and how easy the way back is; density = information per screen without crowding; consistency = same anatomy, tokens, wording and controls as the rest.

## Findings, ranked by severity

Fixed means fixed in the shared layer (brand.css, lib/*, shell.js, shell.css) unless a module file is named.

| # | Severity | Finding | Status | Evidence |
|---|---|---|---|---|
| 1 | Critical | The plan of record could be edited with no friction (v1 to v120 in 30 minutes). Nothing said it drives the pacing goals. | Fixed: `Plan of record` chip with age, strip above every grid, first-edit card (copy / edit / cancel), History quick restore. DESIGN.md 7b. | `$V/por-ask-dark.png`, tests `test_plan_of_record_chip_and_grid_strip`, `test_history_has_a_one_click_restore_for_the_plan_of_record` |
| 2 | Critical | Two tabs (or two people) silently overwrote each other. | Fixed: `expected_version` on series, rename, restore, detach; 409 shows Reload / Overwrite. | test `test_stale_tab_gets_reload_or_overwrite` |
| 3 | High | A failed source rendered as zeros or an empty page. | Fixed: `/run.degraded` banner on every page; 503 `upstream_degraded` becomes a plain card, one banner entry per module. Raw codes ("internal_error") now read as plain words. | tests `test_degraded_modules_show_one_plain_banner`, `test_503_upstream_degraded_becomes_the_banner_not_an_empty_page`, `test_error_codes_are_shown_as_plain_words` |
| 4 | High | 12,376 text nodes under 12px (10.5 and 11.5px type scale). | Fixed: scale is 11 (bold caps only) / 12 / 12.5 / 13. | `$V/before/audit.json` vs `$V/final2/audit.json` |
| 5 | High | 547 contrast failures: projection asterisks at 70% opacity (3.1:1 light), muted-on-tint, disabled-looking active controls, history rows, amber edges. | Fixed with token changes and one removed opacity (`pacing.js`). | `$V/ba-history-light.png` |
| 6 | High | 300 controls with a border or fill under 3:1 (inputs, selects, buttons on sand and amber). | Fixed: `--lv-input-edge` `#A98216`, `--lv-input-grid` for the gridline, button borders on `--lv-line-strong`, error-box buttons in the bad color. | `$V/states-final` |
| 7 | High | Cohort vintage bars and legend swatches were 1.5:1 to 2.5:1 on the card; 2024 to 2027 vintages nearly invisible in dark. | Fixed: `charts.ramp(n, slot)` steps toward ink, not the surface (`cohorts.js` calls it). | `$V/ba-cohorts-chart-dark.png`, `$V/cohorts-fixed-dark.png`, `$V/cohorts-fixed-light.png` |
| 8 | High | Variables scrolled sideways at 390px (about 60px over). | Fixed by the Variables owner; verified: 0 overflow in 72 loads. | `$V/final2/variables-390-*.png` |
| 9 | High | Active tab nearly invisible and cut off at 1024px. | Fixed: 800 weight, 3px ember underline, scrolled into view. | `$V/ba-tab-1024.png` |
| 10 | Medium | Ctrl+Z after a save did nothing (the re-render dropped focus to the body). | Fixed in the grid; focus is kept across `update()`. | test `test_undo_restores_previous_value` |
| 11 | Medium | `(500000)` and `-350k` parsed to negative spend and went to the server. | Fixed: non-negative lines (spend, units, orders, principal, cpa, aov) and values above 99,999,999,999 are refused in the cell before sending; any other server limit is learned from its message. | tests `test_negative_forms_*`, `test_out_of_range_*` |
| 12 | Medium | Month headers said only actual or projected. | Fixed: Books / Feeds / Projected* chips when the payload sends `state`; the headline reads "Books through Oct 2026 · Feeds through Nov 2, 2026". | test `test_month_header_shows_books_feeds_projected`, `test_headline_strip_names_books_and_feeds_dates` |
| 13 | Medium | Deleting a scenario was final; copies made before freezing silently followed their base. | Fixed: Delete toast has Undo, History lists deleted scenarios with days left and Restore; `Detach from <base>` appears for a copy that still follows its base. | tests `test_deleted_scenario_can_be_undone_from_the_toast_and_from_history`, `test_detach_is_offered_only_for_a_copy_that_still_follows_its_base` |
| 14 | Medium | Headline strip said "was" and "vs" nowhere; after a change you could not tell what moved. | Fixed: in the plan of record a moved number shows `was $1.5M`; in a copy each number shows its difference from its base. | `$V/ba-headline-copy.png`, `$V/tasks-after/a-after.png` |
| 15 | High | Cohorts "Save Live as Goal" replaces the Goal curve in one click with no confirm and no Undo. | Ticket: `TICKETS_design_cohorts.md` 1 | `$V/cohorts-save-goal-dark.png` |
| 16 | High | Spend plan "Apply to N months" can overwrite 38 cells with only a 3-second toast and no Undo. | Ticket: `TICKETS_design_drivers.md` 1 (the toast now supports an Undo action; the page must pass it) | `$V/tasks-after/a-after.png` |
| 17 | Medium | Pacing mixes a green sentence with amber and crimson cards; "on track or not" needs a second read. | Ticket: `TICKETS_design_pacing.md` 1, 2 | `$V/tasks-after/e-above-fold.png` |
| 18 | Medium | Inventory supply: the "Data checks" card (8 items) sits between the answer and the reorder table; the table starts at y=871 on a 900px screen. | Ticket: `TICKETS_design_inventory_supply.md` 1 | `$V/tasks-after/c-read.png` |
| 19 | Medium | Spend plan: the copy cue prints the slug ("a copy of current"), Compare is the last block, the compare difference row reads backwards. | Ticket: `TICKETS_design_drivers.md` 2 to 4 | `$V/tasks-after/b-compare.png` |
| 20 | Low | Inventory supply renders about 240 date inputs on a phone. | Ticket: `TICKETS_design_inventory_supply.md` 2 | |
| 21 | Low | Local console shows 404 on `pnl/checks`, `pnl/uploads`, `inventory-supply/history` and 500 on `variables` against the local server; the pages handle it (plain card with Retry) but the endpoints need to exist before the redeploy. | For the core and variables owners. Not a design fix. | `$V/variables-local-500.png` |

## Walkthroughs (1440px, local copy, `vet_tasks.py`)

| Task | Seconds | Actions | Result |
|---|---|---|---|
| e. This month's pacing vs goal | 2.5 | 0 | `/projection` lands on Pacing; TLDR is in the first screen (y 192 to 280). Five-second test passes on the numbers; the sentence mixes verdicts (finding 17). |
| e (phone, 390px) | 0.0 | 0 | TLDR box y 246 to 509 of 844: above the fold. |
| a. Change ad spend from Dec 2026, watch FY27 | 1.7 | 5 | Revenue `$10.4M` becomes `$11.8M was $10.4M`, NOI `$1.5M` becomes `$1.3M was $1.5M`, implied value moves. The first edit in a real session adds one click (the plan-of-record card). |
| b. Clone as Bull, change, compare | 6.1 | 9 | Copy cue appears (amber `Copy`), headline shows `+$935K vs Current plan`. Compare is the last block (finding 19). |
| c. Which product runs out first | 0.1 | 0 | "Vital Bull at the International 3PL runs out Nov 4, 2026*; the order was due Aug 22, 2026." Answer is right; reorder table is below the fold (finding 18). |
| d. QuickBooks upload preview | 1.2 | 2 | Diff shows `15 cells changed`, `2 accounts not mapped` first, button reads "Commit 15 cells"; closed without committing. |
| f. Undo a bad edit, restore a version | 6.1 | 6 | `$99,000,000*` typed, Ctrl+Z restores `$450,000*`, History restore of an older version works behind a plain-words confirm. |
| g. Phone | | | Bar is one row, tabs collapse to one `Projections` menu, TLDR stacks, tables scroll inside their box, no page overflow on any page. |

## What changed in the shared layer

`brand.css` (type floor, input edge and grid tokens, scrim), `lib/grid.js` / `grid.css` (plan-of-record strip, focus kept after save, Undo, input limits, three-state month row, touch rows), `lib/api.js` (write guard, versioned writes, conflict handling, degraded events per module), `lib/scenario.js` (silent create, archived, undelete, detach), new `lib/guard.js` (first-edit card, conflict card), `lib/util.js` (toast actions, plain error words, degraded card), `lib/charts.js` (`ramp`), `shell.js` / `shell.css` (chip, History quick restore and deleted scenarios, degraded banner, headline deltas and books/feeds label, Detach, tab reveal). Module-page edits: `cohorts.js` (vintage colors via `charts.ramp`) and `pacing.js` (asterisk opacity removed). Module contract `render(el, ctx)` is unchanged.

## Tests

`tests/projection/ui` (Playwright against the mock server) and `tests/projection/design` (token contrast, no hardcoded colors, no external requests) are green and run separately. New: `/projection` lands on Pacing when built, Overview at `/projection/overview`, tab order Pacing then Overview then the rest, plan-of-record card and strip, conflict card, degraded banner and 503 card, input limits (up front and learned), books / feeds label, three-state months, detach, deleted-scenario undo, plain error words.

## Tooling notes

`vet.py` and `vet_states.py` are read-only. `vet_tasks.py` writes: run it only against a local copy (it sets the "edit the plan of record" answer up front so it times the edit, not the card). The readability audit now counts a 2px focus-colored outline as a control edge (the in-cell editor draws its edge that way).
