"""Loaders: read the drivers module's inputs from Postgres (read-only) into model.DriverInputs.

Sources and priority (per month, per line):
  channel spend    books: pnl.mkt_* (QuickBooks upload, owned by the pnl module)  >  spend.* (seeded from the sheet / loaded here)
                   feed:  spend.meta_feed (complete months of Meta spend rolled in from ad_daily by etl.projection_drivers.refresh)
                   A month with any books value is a books month; otherwise a month with a feed value is a feed month (Meta exact, the other
                   channels estimated from the channel-mix lever); otherwise the month is planned.
  Amazon / TikTok / DTC revenue   pnl.* (books)  >  amazon_rev / tiktok_rev / dtc_rev (seeded)
  new customers    proj_cohorts (cohort table; new customers and new customers x first-order $)  >  nc.orders / nc.rev (seeded)
  plan and overrides   proj_series 'drivers.<key>' for the scenario, falling back to its base scenario cell by cell
"""
from __future__ import annotations

from datetime import date

from .. import series
from ..freshness import today_pacific
from ..interfaces import Month, Scenario
from .model import META_FEED_KEY, NC_ACTUAL_KEYS, OVERRIDE_KEYS, PLAN_KEYS, QB_CHANNEL_KEYS, REVENUE_KEYS, SERIES_PREFIX, DriverInputs

_COHORT_COLS = ("cohort_month", "new_customers", "first_order_per_customer")


def _all_actual_keys() -> list[str]:
    keys = [META_FEED_KEY, *NC_ACTUAL_KEYS]
    for pair in QB_CHANNEL_KEYS.values():
        keys.extend(pair)
    for pair in REVENUE_KEYS.values():
        keys.extend(pair)
    return keys


def _merge(primary: dict[Month, float], fallback: dict[Month, float]) -> dict[Month, float]:
    out = dict(fallback)
    out.update(primary)
    return out


def cohort_table_nc(conn) -> tuple[dict[Month, float], dict[Month, float]] | None:
    """(new customers, NC revenue) by cohort month from proj_cohorts, or None when the table is absent / has no usable rows."""
    have = conn.execute("SELECT to_regclass('proj_cohorts') AS t").fetchone()["t"]
    if not have:
        return None
    cols = {r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'proj_cohorts'").fetchall()}
    if not set(_COHORT_COLS) <= cols:
        return None
    orders: dict[Month, float] = {}
    rev: dict[Month, float] = {}
    for r in conn.execute("SELECT cohort_month, new_customers, first_order_per_customer FROM proj_cohorts "
                          "WHERE first_order_per_customer IS NOT NULL").fetchall():
        n = float(r["new_customers"])
        orders[r["cohort_month"]] = n
        rev[r["cohort_month"]] = n * float(r["first_order_per_customer"])
    return (orders, rev) if orders else None


def current_date() -> date:
    """Today in Pacific time (the business's calendar); tests patch this."""
    return today_pacific()


def load_inputs(conn, scn: Scenario, today: date | None = None) -> DriverInputs:
    inp = DriverInputs(today=today or current_date())
    plan: dict[str, dict] = {k: {} for k in PLAN_KEYS}
    over: dict[str, dict] = {k: {} for k in OVERRIDE_KEYS}
    for c in series.fetch_cells(conn, scn, prefix=SERIES_PREFIX):
        name = c["line_key"][len(SERIES_PREFIX):]
        target = plan if name in plan else over if name in over else None
        if target is None:
            continue
        target[name][c["month"]] = c["value"]
        inp.cell_info.setdefault(name, {})[c["month"]] = {"kind": c["kind"], "note": c["note"], "origin": c["origin"]}
    inp.plan, inp.overrides = plan, over

    act = series.get_actuals(conn, keys=_all_actual_keys())
    inp.qb_spend = {ch: _merge(act.get(pair[0], {}), act.get(pair[1], {})) for ch, pair in QB_CHANNEL_KEYS.items()}
    inp.meta_feed = dict(act.get(META_FEED_KEY, {}))
    inp.amazon_actual = _merge(act.get(REVENUE_KEYS["amazon_rev"][0], {}), act.get(REVENUE_KEYS["amazon_rev"][1], {}))
    inp.tiktok_actual = _merge(act.get(REVENUE_KEYS["tiktok_rev"][0], {}), act.get(REVENUE_KEYS["tiktok_rev"][1], {}))
    inp.dtc_actual = _merge(act.get(REVENUE_KEYS["dtc_rev"][0], {}), act.get(REVENUE_KEYS["dtc_rev"][1], {}))
    table = cohort_table_nc(conn)
    if table is not None:
        inp.nc_orders_actual, inp.nc_rev_actual, inp.nc_source = table[0], table[1], "cohort table"
    else:
        inp.nc_orders_actual = dict(act.get(NC_ACTUAL_KEYS[0], {}))
        inp.nc_rev_actual = dict(act.get(NC_ACTUAL_KEYS[1], {}))
        inp.nc_source = "loaded new-customer actuals"
    return inp


def feed_gap(inp: DriverInputs) -> dict[Month, tuple[float, float]]:
    """{month: (Meta feed, Meta per the books)} for months that hold both, to show the gap."""
    books = inp.qb_spend.get("meta", {})
    return {m: (v, books[m]) for m, v in inp.meta_feed.items() if m in books}
