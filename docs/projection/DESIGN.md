# Projection design system

Lonvera Projections look and behave like one product. This file is the spec the seven module pages are built against: `cohorts`, `drivers`, `pnl`, `inventory_demand`, `inventory_supply`, `financing`, `pacing`. Live reference: `/static/projection/styleguide.html` (every token and component, light and dark, with the real grid and chart code). Tokens: `app/static/projection/brand.css`. Checks: `tests/projection/design` (`pytest tests/projection/design`).

## Module page checklist (read this, then build)

1. Render into `el` with `ctx.ui.pageHeader({title, purpose, tldr:{line, kpis}, actions})`: title, one-line purpose, a TLDR strip that says the so-what first. 3 to 5 numbers. Never skip the TLDR.
2. The main thing is a grid (`ctx.grid`) or a chart (`ctx.charts`) directly under the header. One short source caption under it (`ctx.ui.source(...)` or the payload's `captions`).
3. Colors: only `var(--lv-*)` from `brand.css`. No hex, rgb or hsl in module code (a test fails the build). Charts: `ctx.charts.PALETTE`, fixed slot order.
4. Status is always icon + word: `ctx.ui.chip('OUT', 'bad')`. Ember orange is for the primary action, the active tab and focus. Never for a number or a status.
5. Months are columns, entities are rows. Input rows amber, calculated rows quiet, totals heavy, projected months blue and asterisked. The grid does this; pass `kind`, `fmt`, `input` correctly.
6. Labels read as a finished document: full words, no build history, no rationale, no "(see ...)". Expand an acronym the first time it appears on the page.
7. Edit in place. No modal for a single value. Save is automatic and shows Saving, Saved or the error. Undo is always one keypress.
8. Every action has a keyboard path. Visible focus. 44px touch targets on phones. Tables scroll inside their own box.
9. Write empty, loading and error states in plain language that say what happened and what to do.
10. Check your page at 1440px and 390px, light and dark (`python tests/projection/design/tools/shoot.py` shows how).

## 1. Brand

Primary source (Josh, 2026-11-03): the **Lonvera "Vital Bull PDP R2" prototype** (`claude.ai/artifact/TrebKS73zj66F4Hzv1FMcE`). It supplied the palette, font, radii, focus ring and the warm, calm, plain feel. Secondary: the Lonvera Brand Guidelines 2026 PDF (personality, Boneflex blue) and `~/lonvera-theme` (mountain mark, `lp-*` product palettes) where the artifact is silent. Where they differ, the artifact wins.

| Value | Where it came from |
|---|---|
| Bone `#F6F2EB` page, Card `#FBF8F3` panels, Sand `#EEE7DA` headers, Parch `#EFE6D7`, Ink `#1C1915`, Rule `#E3DBCE` | artifact `:root` |
| Ember `#C75123` action, Ember deep `#9E3E19` hover and link text, Ember tint `#F6E3D9` | artifact `:root` |
| Night `#161310`, Night-2 `#221D18` (dark bands) | artifact `:root` |
| Amber `#F4B43C` (basis of the input-cell tint and the "copy" scenario cue) | artifact `:root` (stars) |
| Radii 8 / 14 / 20 px, focus ring 2px ember offset, ease `cubic-bezier(.2,.7,.2,1)`, 1px rules, stroke icons (24px, 2px round) | artifact CSS |
| Plus Jakarta Sans 400 to 800 | artifact `--sans`; self-hosted (see Fonts) |
| Muted `#665D53` (artifact `#6E655B`, darkened to pass 4.5:1 on Sand) | adjusted by contrast test |
| Boneflex blue `#4A76AD` / navy family, used only as the hue family of "projected" and the Boneflex chart slot | Brand Guidelines PDF |
| Mountain mark and "LONVERA" wordmark | `lonvera-theme/assets/lp-bl-img-lonvera-logo-dark.png` (560x242 raster) |
| Personality: straight-talking, warm, never preachy; plain words, short sentences, second person | Brand Guidelines PDF |
| Product names: Vital Bull, Beef Liver, Boneflex (never "VitaBull", "Bonefix") | Brand Guidelines PDF |

**Logo.** Only a raster existed (black on transparent PNG). `tests/projection/design/tools/trace_logo.py` traces it into clean vector paths (marching squares, 4x supersampling, 0.35 px tolerance) and writes `assets/lonvera-{logo,mark,wordmark,lockup}-{black,cream}.svg` plus `favicon.svg`. The shapes are the original artwork, vectorized (not redrawn, no font substitution). Ink variant `#0B0A08`, cream variant `#ECE5D9`. Each file is under 4 KB. The app bar shows the word "Lonvera" like the Tags bar (no image), so both sections read as one bar. Use the cream lockup on dark bands and print headers, the black stacked logo on light exports. An optional mark before the brand text is the `.lv-mark` class (a CSS mask of `lonvera-mark-black.svg` that takes the text color); add it to the Tags bar and the Projections bar together or not at all. Never recolor to ember, never stretch, never smaller than 24px high.

**Voice.** Short, plain, second person where addressing the user ("Your edits are safe."). The number comes before the explanation. No marketing tone, no apologies, no exclamation marks, no emoji. No compliance hedging or banned-word lists in any copy.

### Fonts

Plus Jakarta Sans 5.3.0, SIL Open Font License 1.1, from the `@fontsource/plus-jakarta-sans` package (approved by Josh, downloaded once with `npm pack` into a scratch dir). Only the Latin subset, five weights, self-hosted: `assets/fonts/plus-jakarta-sans-latin-{400,500,600,700,800}-normal.woff2` (11.8, 12.3, 12.2, 12.2 and 11.9 KB, about 60 KB in all) plus `OFL-LICENSE.txt`. `@font-face` rules with `font-display: swap` sit at the top of `brand.css`. No runtime font request leaves the app (`test_no_external_requests`). Tabular figures work (verified: "1111" and "0000" measure the same width with `font-variant-numeric: tabular-nums`). The stack falls back to `ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto`.

## 2. Tokens

All in `brand.css`, prefixed `--lv-`. Components use roles, never the raw brand values. The same file maps the existing Tag Explorer names (`--bg --panel --line --line-soft --head-bg --text --muted --accent --accent-bg --good --warn --bad ... --font --mono`) onto the brand, so `app.css` components pick it up without edits. Light is the default. Dark follows `prefers-color-scheme` and is forced with `<html data-theme="light|dark">`. The two dark blocks in `brand.css` are identical (a test checks).

| Group | Tokens |
|---|---|
| Surfaces | `--lv-bg` (page), `--lv-surface` (panels, table body), `--lv-surface-2` (headers, bands), `--lv-surface-3`, `--lv-hover`, `--lv-inverse` (TLDR strip, toasts) |
| Ink and lines | `--lv-ink`, `--lv-ink-2`, `--lv-muted`, `--lv-line`, `--lv-line-soft`, `--lv-line-strong` (3:1 control borders) |
| Action (ember) | `--lv-action`, `-hover`, `-active`, `--lv-on-action`, `--lv-action-tint`, `--lv-link`, `--lv-link-hover`, `--lv-focus`, `--lv-select-bg` |
| Status | `--lv-good`, `-warn`, `-bad`, `-info`, `-neutral` with `-bg` tints; solids `--lv-bad-solid`, `-good-solid`, `-warn-solid`; `--lv-neg` (negative numbers) |
| Data cells | `--lv-input-bg`, `-input-edge`, `-input-hover`, `--lv-calc-ink`, `--lv-total-bg`, `--lv-total-rule`, `--lv-subtotal-bg`, `--lv-band-bg`, `--lv-hl-bg` |
| Month headers | `--lv-actual-head/-ink`, `--lv-proj-head/-ink`, `--lv-proj-cell`, `--lv-proj-rule`, `--lv-proj-band` |
| Charts | `--lv-viz-1..6`, `--lv-viz-ink` (main series), `--lv-viz-neutral` (Other), `--lv-viz-grid`, `--lv-viz-axis`, `--lv-viz-proj-alpha`, `--lv-viz-dash` |
| Type | `--lv-font`, `--lv-mono`, `--lv-fs-2xs 10.5 / xs 11.5 / sm 12 / base 13 / md 14 / lg 16 / xl 20 / 2xl 28 / hero 40`, weights 400 / 500 / 600 / 700 / 800, `--lv-track-kicker` |
| Space, shape | `--lv-sp-1..8` (4, 8, 12, 16, 20, 24, 32, 40), radii `--lv-r-xs 4 / sm 8 / md 14 / lg 20 / pill`, shadows `--lv-shadow-1..3`, `--lv-ease`, `--lv-dur 150ms` |
| Density | `--lv-row-h` 26 (compact) or 34 (comfortable), `--lv-cell-px`, `--lv-col-w` 92 / 100, `--lv-label-w` 250, `--lv-hit` (28, becomes 44 on touch and under 640px) |

### The two color problems, and how they are solved

**1. Ember is close to warn and bad.** Ember (`#C75123`, OKLCH hue 40) is reserved for the primary action, the active tab underline, the focus ring and links (as ember deep `#9E3E19`). It is never used for a number, an alert or a status. Negative numbers and "bad" are **crimson** `#A3173C` (hue 14 against ember's 40, a clear step toward blue-red), always with a minus sign or parentheses, and every status chip carries an icon and a word. Warn is amber-brown on pale amber. `test_ember_is_not_a_status_color` enforces a gap of at least 25 degrees (measured 26). In dark mode the gap is wider (`#F59E74` links at hue 46 vs `#FF92AB` negatives at hue 6).

**2. Yellow input cells on bone.** Input cells use a butter tint (`#FAEBC0`, amber at about 25% on Card) with a slightly stronger amber 1px gridline (`#DDB650`) and semibold ink text. Measured: fill vs calculated cell distance 6.4 in OKLab x100 (unmistakable), contrast with the panel 1.12:1 (not loud), ink on it 14.8:1. Hover deepens to `#F6E0A0`. Editing turns the cell white with a 2px focus ring. In dark mode the tint becomes a deep amber `#3A3011` with cream text. A grayscale printout still shows the amber grid edge.

### Contrast (WCAG), tested

`tests/projection/design/test_contrast.py` parses `brand.css`, resolves every `var()`, and checks 111 token pairs per theme: text 4.5:1 (body, muted, link, status chips and bare status text on every surface, negatives on every row type, TLDR numbers, button labels) and UI/graphics 3:1 (control borders, focus ring on every surface, ember fill, projected rule, six chart colors on page and panel). Result: **all pass in light and dark**. Notable values (light): ink on Card 16.5:1, muted on Sand 5.3:1, white on ember 4.54:1, link `#9E3E19` on Bone 6.0:1, crimson on Card 7.2:1.

### Chart palette

Six categorical slots in a fixed order, never cycled; a seventh series folds into "Other" (`--lv-viz-neutral`) or becomes small multiples. Slots 1 to 3 are the products, so a product always keeps its color:

| Slot | Light | Dark | Use |
|---|---|---|---|
| 1 burnt orange | `#A63D02` | `#B85207` | Vital Bull |
| 2 blue | `#2863AB` | `#3A75BF` | Boneflex |
| 3 green | `#5F983A` | `#5BA84A` | Beef Liver |
| 4 violet | `#8166C3` | `#7C60BD` | 4th series |
| 5 ochre | `#9D7200` | `#976C00` | 5th series |
| 6 teal | `#009394` | `#18A5A5` | 6th series |
| main | ink `#1C1915` | `#F3EEE6` | the series the page is about (total, NOI), or any single series |

Validated with the dataviz validator and ported checks (`test_chart_palette`): lightness band, chroma at least 0.10, adjacent color-blind separation (protan and deutan, OKLab x100) 15.4 light / 17.2 dark (target 8), normal-vision floor 18.7 / 21.0 (floor 15), the three products also pass all-pairs (11.0 / 9.6 color-blind, 24.2 / 24.0 normal), contrast 3.28:1 (light) and 3.38:1 (dark) or better on the panel. Because the chart's job is to be read next to its table, color is never the only identity channel: legend always for 2 or more series, direct labels on at most the last point, the grid beside it is the table view.

**Actual vs projected in charts.** Lines: actual solid 2px, projected dashed `6 4` in the same color. Bars: projected bars at 45% opacity with a 1px outline. A tinted band (`--lv-proj-band`) starts at the first projected month. Tooltips say "(projected)". Bars are at most 24px thick, 4px rounded at the data end, square at the baseline.

## 3. Type

Plus Jakarta Sans. Page title 20px / 800 / -0.02em (`text-wrap: balance`). Section title 14px / 800. TLDR sentence 16px / 600. Purpose line 14px muted. Table and controls 13px (compact) with tabular numerals. Captions 11.5px muted. Kickers and group labels 10.5px / 700 / uppercase / +0.08em. KPI numbers 20px / 800 / -0.02em. Hero figure (one per view at most) 40px, proportional figures. Numbers in columns always `font-variant-numeric: tabular-nums` (the grid does it).

Number formats (from `ctx.fmt`): money `$1,234` (no cents above $1,000; `usd` shows cents for unit prices), negatives `($564,615)` in crimson, zero `—`, percent `12.5%`, multiples `1.86x`, units `1,234`, compact `$6.7M` and `-$350K` in TLDR text, months `Nov 2026`, dates `Nov 5, 2026`. Projected values get a trailing asterisk everywhere, including in the TLDR (`Jan 2027*`).

## 4. Page anatomy

```
+----------------------------------------------------------------------------------------------------------+
| Lonvera  Tags  Projections | Overview | Cohorts  Drivers  P&L soon | Inventory ... | Pacing soon | [Current plan v] v31 [...] |  42px, sticky
+----------------------------------------------------------------------------------------------------------+
| plan headline   Actual through Oct 2026   FY27 revenue $6.7M   FY27 NOI -$3M   Implied EV ...  (hidden until real) |
+----------------------------------------------------------------------------------------------------------+
| page header     Title                                                          [Secondary] [Primary]      |
|                 One-line purpose.                                                                         |
| TLDR strip      Sentence that answers the so-what.            KPI   KPI   KPI   KPI                      |
| main            grid or chart (+ second chart/table)                                                      |
| source caption  QuickBooks through Oct 2026. Projected values are marked *.                               |
+----------------------------------------------------------------------------------------------------------+
```

- **One bar.** Projections and Tags are two sections of the same bar: `Lonvera` (home), `Tags`, `Projections`, the same 42px height, 16px side padding, 14px gaps, 14px bold brand and 13px muted links as the Tags pages. On `/projection` the module tabs follow `Projections` inline, and the scenario control sits at the right end. Switching between Tags and Projections reads as the same bar changing its tabs. Sticky, one row at every width.
- **Module tabs**: Overview plus the seven modules, full height of the bar, thin dividers between the groups (no group labels; the Overview page and the narrow-screen menu keep the group names Plan, Supply and cash, Track). The active tab is 800 weight with a 3px ember underline. A module that is not built yet is muted, medium weight, with a small "soon" tag. When the tabs do not fit (1100px and below) they scroll horizontally inside the bar with a fade at the right edge; at 900px and below they collapse into one `Projections` menu button (the list is grouped, the current page is marked).
- **Scenario control** (right end): a compact select with the scenario name, the version chip (`v31`, hidden under 640px), an amber `Copy` chip when you are not in the plan of record, and a `...` button that opens the actions menu: New scenario, Clone this scenario, Rename, History and restore, and (copies only) Delete this scenario in crimson. Menus are real `role=menu` popups: Enter or click opens, arrows move, Esc closes and returns focus, outside click closes, items are 44px tall on touch.
- **Plan headline** (global strip under the bar, from `GET /run`) shows where the whole plan lands. It is hidden until `/run` returns real numbers: no module built, no figures, or every figure zero. It is not the page's TLDR.
- **Page header** (`ctx.ui.pageHeader`): `h1` title (noun phrase, 1 to 3 words: "Cash and debt", "Inventory supply"), purpose line (one sentence, what the page is for and what to do on it), optional actions on the right (at most one primary), then the **TLDR strip**.
- **TLDR strip** (`ctx.ui.tldr` or `pageHeader`'s `tldr`): a night band. Left: one sentence, at most 18 words, that starts with the finding and includes a number and a time ("Vital Bull runs out in January unless 40,000 units land by December 1."). Right: 3 to 5 KPIs: label (noun phrase, fully spelled), value (formatted, asterisk if projected), optional note (4 words or fewer). Add `tone: 'good'|'warn'|'bad'` only when there is a real threshold; tones use the `--lv-inv-*` colors that pass AA on the night band. If a page has nothing to say yet (no data), the sentence says so ("No purchase orders yet.").
- **Source caption** (`ctx.ui.source(text)` or `captions` in the grid payload): one short line per table or chart: where the data comes from and how fresh. `QuickBooks through Oct 2026.` Not how it is computed.
- **Footer**: none by default. Pages that need a freshness line use `.pg-footer`.

```js
export async function render(el, ctx) {
  const { h, ui, fmt } = ctx;
  const payload = await ctx.api.table(ctx.module.dash);
  el.replaceChildren(h('div', { class: 'pg-page' },
    ui.pageHeader({
      title: 'Inventory supply',
      purpose: 'What runs out, when it has to land, and what to order now.',
      actions: [h('button', { class: 'btn primary', type: 'button' }, 'Add purchase order')],
      tldr: { line: 'Vital Bull runs out in January unless 40,000 units land by December 1.',
              kpis: [{ label: 'First stock-out', value: 'Jan 2027*', tone: 'bad' }, { label: 'Cash for open POs', value: fmt.compact(1.2e6) }] } }),
    h('div'),                                            // grid host: ctx.grid(host, { payload, onEdit })
    ui.source('Warehance shipments through Nov 2, purchase orders as entered.')));
}
```

### Three page types

**A. Monthly grid page** (drivers, pnl, inventory_supply burn, financing cash, cohorts repeat revenue). Header, TLDR, one chart (280px, optional, collapsible on phones), then the grid full width. Grid toolbar: group collapse, FY totals toggle, Undo, Copy; density toggle in the page actions if the page has more than 40 rows. Scenario compare is a strip of chips under the TLDR, not a separate page.

**B. Dashboard or pacing page** (pacing, inventory_demand overview). Header, TLDR, then a `.pg-cards` row of 4 to 6 metric cards (value, target, a pacing bar; green at 100% or more, amber 90 to 100, crimson under 90, inverted for costs, each with an icon and a word), then the main table, then charts in two columns from 1100px up. Freshness chips per feed sit in the page actions ("Meta: through Nov 2", amber when over 2 days old, crimson over 5). Month and grain pickers are segmented controls in the page actions.

**C. Form, upload, preview flow** (QuickBooks upload, cohort upload, intl tracking upload, PO entry). Three labeled steps in one card, one visible at a time: 1 Choose file, 2 Review changes, 3 Commit. Step 1: a dashed drop box that is also a real `<label>` around a file input (keyboard and click), says which file ("QuickBooks Profit and Loss by Month, .xlsx or .csv"). Step 2: three counters (Added, Changed, Blanked) as chips, a diff table (`old` to `new`, changed cells highlighted), unmapped accounts listed first under a warn chip, nothing hidden. Step 3: primary button states the count ("Commit 36 changes"), Cancel beside it, never auto-commit. On success: toast "Saved 36 cells to Oct 2026" and a link to the page that changed. Closed months only; a not-yet-closed month shows an info chip "In progress, skipped".

## 5. Tables (the grid)

- Months are columns, entities are rows. First column (labels) and the header rows are sticky; wide tables scroll inside `.pg-scroll` (never the page). Year band above months; FY toggle adds fiscal-year columns on the right.
- **Row kinds** (payload `kind`): `header` = collapsible section band (uppercase 10.5px on `--lv-band-bg`), `input` = amber, `calc` = quiet ink-2 on panel, `subtotal` = bold, 1px strong rule above, `total` = 800 weight, 2px ink rule above, sand fill, `ratio` = italic muted (percent and multiples).
- **Numbers** right-aligned, tabular. Negatives `($1,234)` crimson (percent and multiples: `-12.5%`), zero `—` muted, missing blank. Never mix units in a row. Money rows are whole dollars unless the unit is small (prices).
- **Projected** months: header `--lv-proj-head` blue with blue ink, a 2px blue rule at the first projected month, a cool whisper tint on cells, an asterisk on every non-zero value. Actual headers are sand. The year band shows a mixed gradient on the year that contains both.
- **Inputs** are amber with an amber gridline, semibold. Hover deepens them and the cursor becomes a text cursor. Calculated cells are never amber and have no hover change, so "can I type here" is answered by color alone plus the cursor.
- **Status text** in a cell (`fmt:'text'`) renders as a chip. `highlight` callbacks add an icon plus tone to a numeric cell (crimson with a cross for bad, amber with a bang for watch, green with a check for good).
- **Density**: compact (26px rows, 13px type) by default; comfortable (34px) via `setOptions({density})` or `data-density="comfortable"`. Phones get 36px rows (touch).
- **Notes**: an "i" beside a label carries the source of an input as a tooltip. Do not put sources in label text.

## 6. Editing

- Click an amber cell (or Enter, F2, double-click, or just type) to edit in place. Enter commits and moves down, Tab and Shift+Tab commit and move sideways, arrows commit and move, Esc cancels, clicking elsewhere commits. Empty or `-` clears the override. Paste tab-separated numbers from Sheets: they fill across the months starting at the selected cell. Delete clears a selection. `350k`, `1.5m`, `12%`, `(1,234)` parse.
- **No modal for a single edit.** The editor is the cell. Invalid input keeps the editor open, outlines it crimson, and the toolbar says why ("Not a number").
- **Micro-states** (cell and toolbar status): pending = cell at 60% opacity, toolbar "Saving…"; saved = 2px green inset flash, toolbar "Saved 3 cells"; error = crimson inset, the value reverts, toolbar and toast say what happened ("Too many changes in a short time. Wait a minute and try again.").
- **Undo**: Ctrl or Cmd+Z and the toolbar Undo button, 50 steps. Restore from History brings back an older version of the whole scenario (section 8).
- Edits write the current scenario only. The scenario bar (next section) always says which one.

## 7. Scenarios: which one am I in

The scenario control is part of the sticky bar, so it is visible on every page and every scroll position:

- **Name** in the select, **version chip** (`v31`), and the **cue**: in the plan of record there is nothing extra (quiet by default); in any other scenario the bar gets a 3px **amber underline** and an amber `Copy` chip with an alert icon (tooltip: "You are in a copy. Edits here do not change the plan of record."). Amber under the bar plus the chip is the "you are editing a copy" signal. The page never shifts when the cue appears (the underline is a shadow).
- Switching re-renders the page and writes `?scenario=` to the URL, so a copied link opens the same scenario.
- On phones (390px) the bar is still one row: `Lonvera`, `Tags`, a `Projections` menu button, the scenario select (name only, about 130px) and the `...` button. The version and `Copy` chip are hidden below 480px, but the amber underline stays, and all actions (New, Clone, Rename, History, Delete) remain in the `...` menu.
- **Compare**: the baseline (usually the plan of record) is a solid ink line; each other scenario is a dashed line in the next slot color and a direct label at its last point. In tables, compare shows `Difference vs <baseline>` columns (value and percent) after the scenario's own columns, never replaces them. Compare is read-only; the page header says "Comparing Bull case with Current plan".
- Clone is the way to try something: the dialog is "Clone scenario", prefilled name "<name> copy".

## 8. Destructive actions and recovery

- **Delete a scenario**: crimson button, then a dialog that explains the scope ("This removes the scenario and all of its inputs. Other scenarios are not affected.") and requires typing the scenario's slug. The plan of record cannot be deleted and shows no Delete button.
- **Restore**: History drawer lists versions with who and what changed (the edited line, month, old to new), a Restore button on every version except the current one, and the copy "goes back to how it was at version N. The current state stays in the history, so this can be undone." Restore is itself a new version.
- **Uploads**: preview then commit, never overwrite a month without showing the diff (page type C).
- Bulk series paste over 500 cells is split by the shell; the toast says the count.
- Never use `window.confirm`. Use `ctx` dialogs (`confirmBox`, `confirmTyped`, `askText`).

## 9. States and copy

| State | Pattern | Example copy |
|---|---|---|
| Loading | spinner + one line, no skeleton wall | "Loading the plan…" |
| Empty | dashed box, what is missing and the one next step | "No purchase orders yet. Add one to see when stock runs out." |
| Error | crimson box with icon, what happened in plain words, what is safe, Retry | "The plan did not load: the server did not answer. Your edits are safe." |
| Stale feed | warn chip with the age | "Meta: 3 days old" |
| Not built | muted card | "Not built yet" |
| Rate limited | error toast | "Too many changes in a short time. Wait a minute and try again." |

Rules: say what happened first, then what to do. No error codes, no stack traces, no "Oops". Keep the user's work. Sentence case. Buttons are verbs ("Add purchase order", "Commit 36 changes"), not "OK".

## 10. Status chips

`ctx.ui.chip(text, tone, {solid, estimate})`. Icon plus word, always. Tones: `good` check, `warn` triangle, `bad` crossed circle, `info` i, `neutral` dash, `estimate` dashed outline with a clock.

| Word | Tone | Where |
|---|---|---|
| OK | good | stock cover above target, feed fresh |
| ORDER SOON | warn | order-by within 30 days |
| LOW COVER | warn | under 1.5 months |
| OUT | bad | zero or negative stock in a month |
| OVERDUE | bad, solid | order-by date already passed |
| LANDED / IN TRANSIT / PLANNED / CANCELLED | info / info / neutral / neutral | PO status |
| ESTIMATE | estimate | values that are not actuals (intl 3PL days since Sep 16) |
| FRESH / n DAYS OLD / STALE | good / warn / bad | data feed age |
| ACTUAL / PROJECTED* | neutral / info | month badges where a header is not available |

## 11. Charts

Chart.js only (vendored). Use `ctx.charts` (line, bar, combo, scenarioCompare). Rules: one y axis (never two scales; use small multiples or an index to 100 instead of dual axes; the demo's secondary percent axis is a library capability, not a pattern to copy); axis ticks are compact (`$600K`); title the metric in the card heading, not the legend; zero baseline for bars; legend on top right for 2 or more series; direct label the last point of at most the series the story is about; gridlines hairline and recessive; no 3D, no gradients, no pies; tooltips show month, series, value, and "(projected)". Every chart has its table next to it as the text alternative, and a canvas `aria-label` that states the measure ("New-customer revenue and NOI by month"). Height 280px, 240px on phones.

## 12. Wording

- **Plain words.** "Cost per new customer", not "NC-CPA". "Net operating income", not "NOI", in labels. Acronyms are allowed only after the page has expanded them once, in the purpose line, a TLDR label or an `<abbr title="…">`. Glossary: NC = new customer, NOI = net operating income, AOV = average order value, ROAS = return on ad spend, MER = marketing efficiency ratio (revenue per ad dollar), EV = enterprise value, FY = fiscal year, TTM = trailing twelve months, PO = purchase order, 3PL = third-party logistics warehouse, COGS = cost of goods sold, FBA = Amazon fulfillment.
- **Labels are a finished document.** No build history ("added Oct 13"), no rationale ("because the sheet did X"), no parenthetical justifications. If a mechanism needs one sentence, it goes in the single source caption or the "i" tooltip.
- **Money and time are explicit.** "Nov 2026", "FY27", "through Oct 2026". Projected gets the asterisk.
- **Verbs and nouns.** Titles are nouns; buttons are verbs; the TLDR is a finding.
- Product names: Vital Bull, Beef Liver, Boneflex.
- No compliance hedging, disclaimers or banned-word lists in any copy.

## 13. Accessibility

- Every action has a keyboard path: tabs and links are real `a` elements, buttons are `button`, the grid is a roving-focus table (arrows, Home, End, PageUp, PageDown, Enter or F2 to edit, Esc, Ctrl+C, Ctrl+Z) with `role` and `aria-label`s from the shell; History closes with Esc; the scenario and page menus are `role=menu` (arrows, Home, End, Esc, focus returns to the button); dialogs trap focus (native `dialog`).
- Focus ring: 2px `--lv-focus` (ember, lighter in dark), 2px offset; verified at 3:1 or better on every surface including amber cells, the dark top bar and the TLDR band. Never `outline: none` without a replacement.
- Color is never the only signal: negatives have a sign or parentheses, statuses have icon and word, projected months have an asterisk, inputs have a cursor and an edge.
- Contrast: see section 2 (all pairs tested).
- Touch: 44px minimum targets under 640px and on coarse pointers (buttons, selects, nav tabs, dialog buttons); grid rows 36px.
- Motion: 150ms color and shadow transitions only; `prefers-reduced-motion` removes all transitions and the spinner animation stops moving the layout.
- Skip link to the main content on every page. `lang="en"`. Canvas charts have labels and a table.
- Live regions: the main area is `aria-live="polite"`, toasts are `role="status"`.

## 14. Mobile (390px)

Reading comes first: Josh reads numbers on a phone. Rules: the page never scrolls sideways; the bar stays one row (42px) with the scenario control reachable (name plus `...` menu) and the module tabs collapsed into one `Projections` menu; the sticky label column (140px) and the header stay while months scroll inside the table box; the TLDR strip stacks (sentence first, KPIs in two columns); the plan headline becomes one scrolling row; charts drop to 240px; action buttons wrap under the title; dialogs are full-width with 44px buttons; menu items are 44px tall. Editing on a phone works (tap a cell, the keyboard opens, "Next" moves on) but the layouts are tuned for reading. Verified at 1440, 1024, 768 and 390px in light and dark (`tests/projection/design/tools/shoot.py`, and a UI test per width).

## 15. Density and dark mode

Compact is the default because the grid is the product. `data-density="comfortable"` is offered by the grid and the style guide. Dark mode is automatic from the OS and can be forced with `data-theme`. Every token pair is tested in both. Charts re-read the CSS palette each time one is drawn (`charts.refreshPalette()`), so a re-render after a theme change picks up the dark steps.

## 16. Do and don't

| Do | Don't |
|---|---|
| "Vital Bull runs out in January unless 40,000 units land by December 1." | "Computed from a trailing four-month window of Warehance units." |
| `($564,615)` in crimson, `—` for zero, `Nov*` for projected | `-564615` in orange, `0` in every empty cell |
| `OUT` with a cross icon and the word | A red dot |
| Ember on "Add purchase order", the active tab and the focus ring | Ember on a negative number or a warning |
| Edit the cell where it sits, Esc to cancel | A modal that asks for one number |
| "No purchase orders yet. Add one to see when stock runs out." | "Error: empty result set" |
| "Net operating income" in the label, an "i" with the source | "NOI (memo — Josh's lever, see Oct 13 change)" |
| One source caption: "QuickBooks through Oct 2026." | A paragraph on how the model works |
| Wide table scrolls inside its box | Page scrolls sideways |
| Amber means "you can type here" and nothing else | Amber for warnings in the same view as input cells (use the warn chip) |

## 17. Files, tests and shell changes

| Path | What |
|---|---|
| `app/static/projection/brand.css` | tokens, fonts, density, base rules |
| `app/static/projection/assets/` | logo SVGs (stacked, mark, wordmark, lockup, black and cream), `favicon.svg`, `fonts/` |
| `app/static/projection/styleguide.html` | live reference, links the real grid and charts |
| `app/static/js/projection/shell.css`, `lib/grid.css` | chrome, page anatomy, controls, chips, dialogs, drawer, grid (all tokens) |
| `tests/projection/design/` | `test_contrast.py` (token pairs, chart palette, ember and input rules, fonts, logos), `test_no_hardcoded_colors.py` (no literal colors or external requests in projection CSS, JS, HTML), `tools/trace_logo.py`, `tools/shoot.py` (screenshots into `shots/`) |

Shell JS edits made for the design (small, the module contract `render(el, ctx)` is unchanged):
- `projection.html`: links `brand.css`; one sticky `<header class="pg-chrome">` holds the whole bar (brand `Lonvera`, `Tags`, `Projections`, `#pg-nav` tabs, `#pg-navmenu`, `#pg-bar`); SVG favicon, skip link, `color-scheme` meta, title "Projections · Lonvera".
- `shell.js`: the bar is static markup plus `buildNav` (inline tabs and the narrow-screen `Projections` menu) and `buildBar` (select, version chip, `Copy` chip `#pg-mode`, `...` actions menu `#pg-menu-btn` / `#pg-menu-pop` with `data-act` items); small `wireMenu` helper (role=menu, keyboard); sets `body[data-pg-scenario="default|copy"]`; group names (Plan, Supply and cash, Track) appear in the Overview cards and the narrow menu (the `data-group` values stay P1, P2, P3); the select no longer appends "(default)"; the headline strip stays hidden until `/run` returns a non-zero figure; document titles read "Projections · Lonvera" and "<Module> · Projections · Lonvera"; the Overview page uses `pageHeader` and a one-line purpose per module.
- `lib/util.js`: `ui.chip`, `ui.tldr`, `ui.pageHeader`, `ui.source` (reachable as `ctx.ui.*`).
- `lib/charts.js`: palette, grid, axis, tooltip and projected-band colors read from `brand.css`; bars capped at 24px with 4px rounded ends; a lone series draws in ink; `scenarioCompare` baseline is ink.
- `_demo.js`: shows `pageHeader` with a TLDR; chart colors from the palette.
- `lib/grid.css` and `shell.css` were rewritten on tokens with the same class names and DOM (comfortable column stays 100px).
- UI tests: the scenario action buttons moved into the `...` menu, so `test_shell.py` clicks them through an `act(page, name)` helper (open the menu, pick the item); the nav-group labels no longer exist in the bar. New tests cover the single bar structure, quiet unbuilt tabs, the menu's keyboard behavior and the copy cue, one row at 1440 / 1024 / 768 / 390px, the zero-value headline and the document titles.

## 18. Open questions

1. **Edit lock.** Every scenario is editable at all times; safety is the amber cue, undo and History. If a misclick on the plan of record is still a worry, add a "Lock the plan of record" toggle (view-only until unlocked); the grid already has `readOnly`.
2. **Plan headline vs page TLDR.** The shell's global headline strip stays (where the whole plan lands, hidden until real). Each page adds its own TLDR. If that feels like two strips, hide the global one on module pages.
3. **Shared look with the Tags pages.** The Tags pages do not link `brand.css`, so the two bars match in structure and size but not yet in font, surface and accent (system font and pure white there; Plus Jakarta Sans and bone here). Linking `brand.css` in `index.html` and adding `.lv-mark` to both bars finishes the match; that file is the Tags owner's.
4. **Logo fidelity.** The mark is traced from a 560px raster; at 30px it is exact, above about 200px high the edges would show tracing. Send the original vector if one exists.
