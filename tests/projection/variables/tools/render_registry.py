"""Writes docs/projection/VARIABLES.md from the code registry (one source) plus the audit tables kept in this file.

    python tests/projection/variables/tools/render_registry.py          # rewrite the doc
    python tests/projection/variables/tools/render_registry.py --check  # exit 1 when the doc is out of date (a test runs this)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
from app.projection.variables import registry as R          # noqa: E402
from app.projection.variables.spec import MODULE_LABELS, MODULES   # noqa: E402

DOC = ROOT / "docs" / "projection" / "VARIABLES.md"

# ---- (b) numeric literals inside formulas: decision per pattern (tab, cells, literal, what it is, decision, variable)
LITERALS = [
    ("P&L", "V17", "1499426", "Aug-26 repeat revenue typed as a total minus NC revenue", "variable", "drivers.repeat_rev_override"),
    ("P&L", "W17", "0.5, 0.5", "Sep-26 repeat revenue = half waterfall, half month-to-date pacing", "variable, not carried (wired to one column)", "drivers.pacing_blend"),
    ("P&L", "BS18", "0.1", "Chart helper block", "structural", ""),
    ("P&L", "V21:BJ21", "0.07", "Sales-tax estimate on DTC revenue", "variable", "drivers.sales_tax_rate"),
    ("P&L", "D46", "22631.4", "Feb-25 inventory shrinkage added inside a total", "history", "pnl.driver.inventory_shrinkage"),
    ("P&L", "V56:BJ56", "0.005", "TikTok ads = 0.5% of Facebook spend", "variable (differs from the trailing mix)", "drivers.channel_share.tiktok"),
    ("P&L", "U63", "4143.5, 3000", "Jul-26 marketing software typed as a sum", "history", "pnl.marketing_software_jul26"),
    ("P&L", "W75:BJ75", "15000, 20000000, 5000000, 15000", "Headcount: base, revenue threshold, step width, cost per step", "variable x4", "pnl.headcount.base, threshold, step, increment"),
    ("P&L", "AI120", "4, 0.21", "Tax on Sep-Dec 2026: 4 of 12 months, 21%", "variable (rate); the 4 is the period", "financing.tax.rate, financing.tax_payments"),
    ("P&L", "AR120:AU120", "0.21", "Tax on 2027 and 2028 net operating income", "variable", "financing.tax.rate"),
    ("P&L", "H145:BJ157", "6", "Months in the rolling growth averages", "variable", "pnl.trend.avg_window"),
    ("P&L", "C162:H162", "3 to 8", "Enterprise value multiples", "variable", "pnl.ev.multiples"),
    ("P&L", "O193:P202", "5000", "Retainer added to each bonus", "variable (linked to the retainer input)", "financing.payout.lazar"),
    ("P&L", "H194:I202", "1.16, 1.15, 1.1, 1.12", "Bonus step-ups per scale tier", "variable", "financing.payout_columns"),
    ("Payback", "E118:U118", "3 to 19", "Month offsets from Jan 2025", "structural (a month index)", ""),
    ("Payback", "B133:U166", "3 to 36", "Month of age in each running-cumulative row", "structural; the horizon is a variable", "drivers.payback.horizon_months"),
    ("Hist Waterfall - Last 4 Months Curve", "C82:CM82", "60", "Repeat stops after month 60", "variable", "cohorts.max_age"),
    ("Cohort Data - Unified", "H36:EZ36", "3, 3", "Last 3 cohorts averaged; at least MIN(3, count)", "variable x2", "cohorts.n_cohorts, cohorts.min_cohorts_per_age"),
    ("COGS", "O5:P7", "3, 5", "Cost of a 3-bottle and a 5-bottle bundle", "structural (display)", ""),
    ("COGS", "J32:J33", "0.5", "Half of a unit cost, display", "structural (display)", ""),
    ("Automated Pacing", "D6, D10, D20:D21, D46", "7", "Last 7-day average window", "variable", "pacing.last_days"),
    ("Automated Pacing", "K6", "16", "Stray divide by 16 in a helper cell", "structural (helper)", ""),
    ("Automated Pacing", "J7", "35000", "Stray +35,000 on the repeat projection", "not carried (no output reads it)", "pacing.projection_offset"),
    ("Automated Pacing", "M17", "24", "Hours in a day", "structural", ""),
    ("Month Snapshot", "L8:L19", "3, 4 .. 12", "Months of age and 'last 3 cohorts' in the repeat table", "structural; the 3 follows cohorts.n_cohorts", "cohorts.n_cohorts"),
    ("Purchase Orders", "F2:F74", "7", "Weeks to days in order date = land date - 7 x lead weeks", "structural", ""),
    ("Purchase Orders", "G13, G17:G38, G40", "30, 31, 120", "Land dates chained as a day offset from another order", "not variables (stored as plain dates per order)", "inventory_supply.po_land_date_offsets"),
    ("Rollup - Monthly (MoM) / Weekly / Pacing - Daily / Meta tabs", "per 1,000 columns", "1000", "Cost per 1,000 and CPM", "structural", ""),
    ("Rollup - Weekly / Pacing - Daily / Meta - Weekly", "week columns", "3, 4, 6, 7", "ISO week arithmetic", "structural", ""),
    ("Meta - Daily (180d) / Weekly / Monthly", "A5:D5, B5", "184, 31, 11", "Window of days, weeks and months shown", "variable", "pacing.rollup_days, pacing.rollup_weeks"),
    ("Pacing - Daily (180d)", "BS679", "79769, 822", "A typed quotient left in a data cell", "structural (stray)", ""),
    ("Warehance Weekly", "B4:J12", "6, 7", "Week length", "structural", ""),
]

SELECTORS = [
    ("P&L!A2", "Repeat Case (Last 4 Months Curve)", "Choose the curve behind repeat revenue", "cohorts.curve (live, goal or blend)"),
    ("P&L!A3", "Rev Scenario (Flat)", "Retired scenario selector", "Scenario picker in the bar; scenarios are named copies"),
    ("P&L!A4", "Opex Inflation", "Annual inflation on operating costs", "pnl.engine.opex_inflation"),
    ("P&L!A5", "COGS Inflation", "Annual inflation on cost of goods", "pnl.engine.cogs_inflation"),
    ("Spend-ROAS Matrix!B3", "View: CM or Fully Loaded", "Profit basis of the grid", "financing.matrix.basis"),
    ("Spend-ROAS Matrix!B4", "Target payback period (months)", "Month the profit is read at", "financing.matrix.target_month"),
    ("Month Snapshot!B3", "Month", "Month of the actual / projected / target table", "Pacing page month picker (view selector, kept in the URL)"),
    ("Automated Pacing!M15", "Time override", "Replaces the clock in the intraday projection", "Not carried: pacing.time_override"),
    ("Term Debt Monthly Paydown!B6 / E10:E55", "Fixed payment or typed principal", "How principal is set", "financing.debt.mode"),
    ("Payback!B7, B8", "Today's order value and cost per customer", "Live from Automated Pacing unless typed", "financing.payback.aov, financing.payback.cac (blank = live)"),
]

COVERAGE = {
    "P&L": "Rows 11-19 typed months: drivers.spend_total, nc_cpa, nc_aov, amazon_rev, tiktok_rev and the four typed overrides; payout block F180:J208: financing.payout.*",
    "Payback": "D11 and B12: drivers.payback.product_cost_pct and fulfillment_per_order (one store, also read by Cash and debt and pacing); B7, B8: financing.payback.aov and cac (blank = live); rows 59, 112, 113, 127 are results",
    "Term Debt Monthly Paydown": "B6: financing.debt.fixed_payment (B4, B5 are typed plain cells: financing.debt.principal and rate)",
    "Curves": "C2: cohorts.tail_decay",
    "Vendor COGS": "Unit costs: inventory_supply.price.*.unit_cost; lead weeks: inventory_supply.vendor.*.lead_weeks; the 160 empty yellow cells are spare rows",
    "COGS": "F4:F6 and F32:F34: pnl.cogs.mix.*; D4:D13: pnl.cogs.china_cost.* (also the stock unit value); D32:D43: pnl.cogs.us_cost.*; L37, L38: pnl.cogs.*price*",
    "COGS Adjustments": "Not read by any projected cell (pnl.cogs_adjustments, not carried)",
    "Spend-ROAS Matrix": "B3, B4, B7: financing.matrix.basis, target_month, fixed_cost",
    "Automated Pacing": "M14: pacing.todays_meta_spend (not carried); A4, A31 are labels",
    "Month Snapshot": "B3: pacing.month_snapshot_picker (view selector)",
    "Purchase Orders": "inventory_supply.purchase_orders (74 rows; units, dates, status, paid flags, deposit share)",
    "FBA Transfers": "inventory_supply.fba_transfers (44 rows)",
    "Warehance Fcst Inputs": "inventory_demand.revamp.*; reorder rows: inventory_supply.reorder.revamp.*; stock: inventory_supply.stock_counts",
    "Amazon Fcst Inputs": "inventory_demand.amazon.*; FBA target cover: inventory_supply.fba_target_months; stock: inventory_supply.stock_counts",
    "Intl Fcst Inputs": "inventory_demand.intl.*; reorder rows: inventory_supply.reorder.intl.*; stock: inventory_supply.stock_counts",
    "Cohort Data - Unified": "F27:F34 'First Order (Realized)' typed per cohort: cohorts.cohort_table",
}


def esc(t) -> str:
    return str(t).replace("|", "\\|").replace("\n", " ")


def fmt_default(s) -> str:
    d = s.default
    if s.kind != "scalar":
        return f"{s.series_cells} typed cells" if s.kind == "series" and s.series_cells is not None else "rows on the module page"
    if d is None:
        return "computed" if s.binding == "pnl_driver" else "blank"
    if isinstance(d, list):
        return ", ".join(f"{x:g}" if isinstance(x, (int, float)) else str(x) for x in d[:6]) + (f", +{len(d) - 6}" if len(d) > 6 else "")
    if isinstance(d, float):
        return f"{d:.6g}"
    return str(d)


def render() -> str:
    specs = R.all_specs()
    c = R.counts()
    yellow = json.loads((HERE.parent / "fixtures" / "yellow_cells.json").read_text())
    n = len(specs)
    series = [s for s in specs if s.kind == "series"]
    tables = [s for s in specs if s.kind == "table"]
    cells = sum(s.series_cells or 0 for s in series)
    out: list[str] = []
    w = out.append
    w("# Variables registry")
    w("")
    w("What this is: every input that drives the Lonvera Projections model, as a named, scenario-aware variable with the sheet's value as its default, the sheet cell it came from, and how the engine uses it. "
      "The page `/projection/variables` lists and edits them; `app/projection/variables/specs_<module>.py` is the single source and this file is generated from it "
      "(`python tests/projection/variables/tools/render_registry.py`; a test fails when the two differ).")
    w("")
    w("## TLDR")
    w("")
    t = c["_total"]
    w(f"- {n} variables: {t['IMPLEMENTED']} implemented (a test moves each one and an engine output changes), {t['PARTIAL']} partly wired, {t['MISSING']} sheet items the model does not carry.")
    w(f"- {sum(1 for s in specs if s.kind == 'scalar' and s.status != 'MISSING')} are single numbers or switches, edited in place on the page; {len(series)} are monthly series ({cells} typed months in the sheet) and "
      f"{len(tables)} are tables (orders, cohorts, rows), edited on their module page with a summary and a link here.")
    w("- Gaps still open are in `docs/projection/TICKETS_<module>.md`: one cost line whose driver cannot be saved, feed rules fixed in code, the TikTok ads formula, and intraday inputs the pacing module does not take. Closed during the build: one store for the payback inputs (`drivers.payback.*`), one store for the China landed cost (`pnl.cogs.china_cost.<sku>`), and typed demand rates above 1.")
    w("")
    w("## Counts")
    w("")
    w("| Module | Implemented | Partly wired | Not in the model | Total | Single numbers | Series | Tables |")
    w("|---|---|---|---|---|---|---|---|")
    for m in MODULES:
        ms = R.for_module(m)
        w(f"| {MODULE_LABELS[m]} (`{m}`) | {c[m]['IMPLEMENTED']} | {c[m]['PARTIAL']} | {c[m]['MISSING']} | {len(ms)} | {sum(1 for s in ms if s.kind == 'scalar')} | {sum(1 for s in ms if s.kind == 'series')} | {sum(1 for s in ms if s.kind == 'table')} |")
    w(f"| **All** | **{t['IMPLEMENTED']}** | **{t['PARTIAL']}** | **{t['MISSING']}** | **{n}** | **{sum(1 for s in specs if s.kind == 'scalar')}** | **{len(series)}** | **{len(tables)}** |")
    w("")
    w("Status meaning: **IMPLEMENTED** the engine reads it and a test (`tests/projection/variables/test_variables_access.py`) moves it and sees an engine output change (or the planner's proposal for planner rules); "
      "**PARTIAL** it is exposed but stored twice, only changes what a page shows, or has no edit path yet; **MISSING** the sheet has it (a yellow cell or a number inside a formula) and the app does not, with the reason.")
    w("")
    w("## How the sheet was audited")
    w("")
    w("1. **Typed inputs.** The live sheet's cell fills were read read-only (`tools/fetch_sheet_fills.py`, yellow = Josh's input convention) and joined to the frozen snapshot (`tools/yellow_inventory.py` -> `fixtures/yellow_cells.json`). Plus every typed number in a forward month of a formula row, every column B driver on the P&L, and the levers on Payback, Term Debt, Curves, Fixed Expenses, Vendor COGS, COGS, the three Fcst Inputs tabs, Purchase Orders, Automated Pacing and the payout block.")
    w("2. **Literals inside formulas.** `tools/formula_literals.py` parses every formula of the 40 model tabs, drops strings, references, function names, dates, and 0 / 1 / 2 / 12 / 100, groups formulas that differ only in references, and lists what remains (33 patterns). Each is classified below.")
    w("3. **Selectors.** Dropdown and switch cells, below.")
    w("4. **Code.** `tools/code_literals.py` lists numeric constants in the seven module packages; each is a lever default, a validation bound, or a gap (`TICKETS_<module>.md`).")
    w("")
    w("### Input cells by tab")
    w("")
    w("| Tab | Yellow cells | Typed values | Formulas | Empty | Covered by |")
    w("|---|---|---|---|---|---|")
    for tab, d in yellow.items():
        w(f"| {esc(tab.strip())} | {d['cells']} | {d['typed']} | {d['formula']} | {d['empty']} | {esc(COVERAGE.get(tab.strip(), COVERAGE.get(tab, 'see registry')))} |")
    w("")
    w("A yellow formula cell is a computed result the sheet highlights (for example `P&L` rows 11-17 across all months); it is not an input. Every yellow typed cell above is covered.")
    w("")
    w("### Numbers inside formulas")
    w("")
    w("| Tab | Cells | Literal | What it is | Decision | Variable |")
    w("|---|---|---|---|---|---|")
    for tab, cells_, lit, what, dec, var in LITERALS:
        w(f"| {esc(tab)} | {esc(cells_)} | {esc(lit)} | {esc(what)} | {esc(dec)} | {esc(var) if var else ''} |")
    w("")
    w("### Selectors and switches")
    w("")
    w("| Sheet cell | Name | What it does | In the app |")
    w("|---|---|---|---|")
    for cell, name, what, app in SELECTORS:
        w(f"| {esc(cell)} | {esc(name)} | {esc(what)} | {esc(app)} |")
    w("")
    w("## Registry")
    w("")
    w("Columns: id; variable; kind (value, series, table); unit; the sheet's value; sheet reference; status; how the engine uses it; notes and ticket. A value in brackets after the status means a scope: `all` = shared by every scenario, `feed` = a loader rule applied when data loads.")
    for m in MODULES:
        ms = R.for_module(m)
        w("")
        w(f"### {MODULE_LABELS[m]} (`{m}`): {len(ms)} variables")
        w("")
        w("| Id | Variable | Kind | Unit | Sheet value | Sheet reference | Status | Used by | Notes |")
        w("|---|---|---|---|---|---|---|---|---|")
        for s in ms:
            status = s.status + (f" ({s.scope})" if s.scope != "scenario" else "")
            note = s.notes + (f" Ticket: {s.ticket}." if s.ticket else "") + (" Also read by " + ", ".join(s.also_used_by) + "." if s.also_used_by else "")
            kind = {"scalar": "value", "series": "series", "table": "table"}[s.kind]
            w(f"| `{s.id}` | {esc(s.label)} | {kind} | {esc(s.unit)} | {esc(fmt_default(s))} | {esc(s.sheet_ref)} | {status} | {esc(s.where_used)} | {esc(note.strip())} |")
    w("")
    return "\n".join(out)


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        return 0 if DOC.exists() and DOC.read_text() == text else 1
    DOC.write_text(text)
    print(f"wrote {DOC} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
