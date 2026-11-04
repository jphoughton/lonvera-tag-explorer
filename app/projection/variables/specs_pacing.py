"""Variables of module `pacing`: thresholds, windows, cost-line drivers and the feed-classification rules. The lever list is the module's own SPECS (one source);
the four levers owned by other modules (sales-tax rate, product cost, fulfillment, payback horizon) are listed under their owners."""
from __future__ import annotations

from .build import Builder

TYPE = {"fraction": "fraction", "days": "integer", "weeks": "integer", "months": "integer", "dollars": "money", "month": "month", "rows": "list", "map": "list"}
USED = {
    "pace_good": "Revenue and profit at or above this share of goal read as on pace (green).",
    "pace_warn": "Revenue and profit below this share of goal read as well behind (red); between the two it reads as watch (amber).",
    "cost_warn": "Costs above this share of goal read as well over plan.",
    "early_days": "With fewer full days than this the page warns that projections swing.",
    "last_days": "Days in the 'last N-day average' column and the run-rate projection.",
    "data_lag_days": "Days elapsed = days of the month up to today minus this lag; moves every month-to-date projection.",
    "rates_month": "Plan month whose cost rates price every day of the EBITDA estimate.",
    "below_ebitda_rows": "P&L rows treated as below EBITDA when the EBITDA goal is built from net operating income.",
    "cost_drivers": "Per cost line: rate on Amazon revenue, DTC revenue or total revenue, or fixed dollars.",
    "payback_bad_months": "Payback this many months over goal reads as well behind.",
    "stale_bad_days": "A feed older than this reads as stale (red).",
    "rollup_days": "Days shown by default on the day grain.",
    "rollup_weeks": "Weeks shown by default on the week grain.",
}
ENGINE = {"data_lag_days"}            # the pacing levers whose value reaches engine.run; the rest are applied when a page is built
ALT = {"rates_month": {"alt": "2026-10"}, "below_ebitda_rows": {"alt": [62]}}
VIEW_ONLY = {"pace_good", "pace_warn", "cost_warn", "early_days", "payback_bad_months", "stale_bad_days", "rollup_days", "rollup_weeks", "last_days", "rates_month", "below_ebitda_rows"}


def specs():
    from ..pacing import levers
    b = Builder("pacing", "pacing")
    for s in levers.SPECS:
        if s.owner != "pacing":
            continue
        vt = TYPE.get(s.unit, "number")
        kw = dict(group="Thresholds and windows" if s.key in VIEW_ONLY and s.key not in ("rates_month", "below_ebitda_rows") else "Goal building", status="IMPLEMENTED")
        if s.key == "cost_drivers":
            b.table("cost_drivers", "Cost line drivers for the daily estimate", group="Goal building", sheet=s.source, used=USED[s.key], unit="lines", scope="scenario")
            continue
        b.lever(s.key, s.label, unit=s.unit, vt=vt, default=s.default, path=s.path, lo=s.lo, hi=s.hi, step=1 if s.integer else None, sheet=s.source, used=USED.get(s.key, s.help or s.label),
                probe="engine" if s.key in ENGINE else "view", sens=False, extra=ALT.get(s.key), **kw)
    G = "Feed rules (apply when a day loads)"
    b.table("feed.stage_keyword", "Campaign-name keyword that marks a test campaign", group=G, sheet="meta_campaign_history.classify_stage: name contains 'test'", unit="text",
            used="Splits Meta spend into testing and scaling.", scope="feed")
    b.table("feed.product_rules", "Campaign-name patterns for the Meta product groups", group=G, sheet="meta_campaign_history.classify_product", unit="patterns",
            used="Assigns a Meta campaign to Vital Bull, Beef Liver or Boneflex.", scope="feed")
    b.table("feed.product_level_rules", "Campaign and ad-set patterns for spend by product", group=G, sheet="Meta Spend - By Product sync", unit="patterns",
            used="Assigns Meta spend lines to a product for the per-product bands.", scope="feed")
    b.table("hourly_spend_curve", "Share of a day's Meta spend by hour (24 values)", group="Goal building", sheet="Automated Pacing!M23:N46 (30-day spend by hour)",
            used="Projects end-of-day Meta spend from spend so far today.", unit="values", scope="all")
    b.missing("month_snapshot_picker", "Month shown on Month Snapshot", group="Selectors", unit="month", vt="month", sheet="Month Snapshot!B3 (yellow)",
              used="Chooses the month of the actual / projected / target table.", notes="A view selector, not scenario state: the Pacing page holds it in the URL.", scope="feed")
    G = "Typed inputs on the Automated Pacing tab"
    b.missing("todays_meta_spend", "Meta spend so far today", group=G, unit="dollars", vt="money", default=7528.0, sheet="Automated Pacing!M14 (typed, yellow)", sheet_cells=(("Automated Pacing ", "M14"),),
              used="Projects end-of-day Meta spend with the hourly curve.", notes="Typed by hand in the sheet each day; the app reads spend from the hourly feed.", scope="feed", ticket="pacing#typed-intraday-inputs")
    b.missing("time_override", "Time of day override for the intraday projection", group=G, unit="time", vt="text", default=None, sheet="Automated Pacing!M15 (optional, blank)",
              used="Replaces the current time in the share-of-day lookup.", scope="feed", ticket="pacing#typed-intraday-inputs")
    b.missing("hourly_curve_days", "Days in the hourly spend curve", group=G, unit="days", vt="integer", default=30, sheet="Automated Pacing!M20 (typed 30)", sheet_cells=(("Automated Pacing ", "M20"),),
              used="Average spend per day in each hour = hourly total / this.", scope="feed", ticket="pacing#typed-intraday-inputs")
    b.missing("projection_offset", "Dollars added to the repeat-revenue projection", group=G, unit="dollars", vt="money", default=35000.0, sheet="Automated Pacing!J7 (=H7+35000, literal)",
              used="A side figure on the sheet; no goal or status reads it.", notes="A stray literal in a helper cell.")
    return b.specs
