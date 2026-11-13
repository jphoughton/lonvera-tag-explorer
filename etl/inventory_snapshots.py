"""Daily stock snapshots for the two locations that have no automatic stock feed: Amazon FBA and the international 3PL.  -> proj_demand_snapshot

  python -m etl.inventory_snapshots                 write today's row (and any missing day since 2026-11-03) per product for locations amazon and intl
  python -m etl.inventory_snapshots --dry-run       compute and print, write nothing

Why an estimate: ExpandFi (merchant 1452) holds no FBA inventory table (its tables are sales, settlements, ads, customers, Shopify orders; the live
inventory pull in ExpandFi Dash is on-demand and keeps no history), and the international 3PL has only a portal reading Josh types in. So both locations use the
same method, the typed count carried forward:

    units on hand on day X = latest typed count (as_of <= X)  -  units shipped or sold on the days count date .. X-1  +  units received after the count date up to X-1

A snapshot dated X is the stock at the start of day X (what the Warehance snapshot of that morning also is), so day X's own shipments are not subtracted.
Shipped units come from proj_units_daily (amazon: ExpandFi units; intl: the Warehance non-US estimate). Days the units feed has not loaded yet (Amazon lags about
2 days) use the mean of the last 7 loaded days of that product; the next nights replace those estimates with the real units (estimate rows of the trailing
14 days are recomputed every night).  Receipts: amazon = proj_fba_transfer with status 'received' (receive_date), intl = proj_po rows with warehouse 'intl'
and status 'landed' (land_date).  Planned, ordered and in transit rows never count: nothing is added until it is marked landed or received.  Floor at 0.
Allocated units are not known for an estimate and are stored as 0.

Rules (live data is sacred):
  * Real rows are never overwritten.  A row whose source is not `estimate_from_count` (Warehance, a typed count) is left alone.
  * The day of a typed count gets a real row (source `typed_count`, the count's on-hand and allocated); an estimate already stored for that day is replaced by it.
    When Josh types a newer count, later estimates restart from it.  A typed count that is edited afterwards does not change its stored snapshot row.
  * Append only before the trailing 14 days: a missing day is added, a stored estimate older than 14 days is not recomputed.
  * Nothing before 2026-11-03 (the first Warehance snapshot day) is written.
  * Read-only against every outside system: this job reads three tables of the app's own database and writes proj_demand_snapshot only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

from . import db

PT = ZoneInfo("America/Los_Angeles")
FLOOR = dt.date(2026, 11, 3)                 # first day of the Warehance snapshot series: nothing earlier is written
LOCATIONS = ("amazon", "intl")
EST_SOURCE = "estimate_from_count"
REAL_SOURCE = "typed_count"
REFRESH_DAYS = 14                            # stored estimates this recent are recomputed every night (units arrive late)
MEAN_DAYS = 7                                # a day the units feed has not loaded yet = the mean of this many loaded days


def pacific_today(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(PT).date()


def _fill_units(by_day: dict, latest: dt.date, d: dt.date) -> tuple[float, bool]:
    """Units of one product on day d: the stored value (0 when the feed loaded that day without a row), or the 7-loaded-day mean after the feed's last day."""
    if d <= latest:
        return float(by_day.get(d, 0.0)), False
    window = [latest - dt.timedelta(days=i) for i in range(MEAN_DAYS)]
    return sum(float(by_day.get(x, 0.0)) for x in window) / MEAN_DAYS, True


def compute(counts: dict, units: dict, latest: dict, receipts: dict, existing: dict, today: dt.date) -> tuple[list[tuple], dict]:
    """Pure core. -> (rows (location, product, as_of, on_hand, allocated, source), stats).

    counts    {(loc, product): [(as_of, on_hand, allocated), ...]}  real typed counts only
    units     {(loc, product): {day: units}}
    latest    {loc: last day the units feed has loaded for that location}
    receipts  {(loc, product): [(date, units), ...]}
    existing  {(loc, product, as_of): source} of proj_demand_snapshot
    """
    rows: list[tuple] = []
    stats = {"typed": 0, "estimated": 0, "kept_real": 0, "kept_old_estimate": 0, "filled_unit_days": 0, "skipped_no_units": [], "skipped_no_count": []}
    for loc in LOCATIONS:
        pairs = sorted(k for k in counts if k[0] == loc)
        if not pairs:
            stats["skipped_no_count"].append(loc)
            continue
        if loc not in latest:
            stats["skipped_no_units"].append(loc)
            continue
        for key in pairs:
            _, prod = key
            hist = sorted(counts[key])
            d = FLOOR
            while d <= today:
                prior = [c for c in hist if c[0] <= d]
                d0 = d
                d += dt.timedelta(days=1)
                if not prior:
                    continue
                as_of, on_hand, allocated = prior[-1]
                ex = existing.get((loc, prod, d0))
                if as_of == d0:
                    if ex is None or ex == EST_SOURCE:
                        rows.append((loc, prod, d0, float(on_hand), float(allocated), REAL_SOURCE))
                        stats["typed"] += 1
                    else:
                        stats["kept_real"] += 1
                    continue
                if ex is not None and ex != EST_SOURCE:
                    stats["kept_real"] += 1
                    continue
                if ex == EST_SOURCE and d0 < today - dt.timedelta(days=REFRESH_DAYS):
                    stats["kept_old_estimate"] += 1
                    continue
                by_day = units.get(key, {})
                shipped = 0.0
                x = as_of
                while x < d0:
                    u, filled = _fill_units(by_day, latest[loc], x)
                    shipped += u
                    stats["filled_unit_days"] += 1 if filled else 0
                    x += dt.timedelta(days=1)
                received = sum(float(u) for dd, u in receipts.get(key, []) if as_of < dd < d0)
                rows.append((loc, prod, d0, max(0.0, round(float(on_hand) - shipped + received, 2)), 0.0, EST_SOURCE))
                stats["estimated"] += 1
    return rows, stats


def load_inputs(conn) -> tuple[dict, dict, dict, dict, dict]:
    counts: dict = defaultdict(list)
    for loc, prod, as_of, oh, al in conn.execute("SELECT location, product, as_of, on_hand, allocated FROM proj_stock_snapshot "
                                                 "WHERE location = ANY(%s) AND NOT estimate ORDER BY as_of", (list(LOCATIONS),)):
        counts[(loc, prod)].append((as_of, oh, al))
    units: dict = defaultdict(dict)
    latest: dict = {}
    for loc, prod, day, u in conn.execute("SELECT location, product, day, units FROM proj_units_daily WHERE location = ANY(%s) AND day >= %s",
                                          (list(LOCATIONS), FLOOR - dt.timedelta(days=400))):
        units[(loc, prod)][day] = u
        if loc not in latest or day > latest[loc]:
            latest[loc] = day
    receipts: dict = defaultdict(list)
    for prod, rd, u in conn.execute("SELECT product, receive_date, units FROM proj_fba_transfer WHERE status = 'received'"):
        receipts[("amazon", prod)].append((rd, u))
    for prod, ld, u in conn.execute("SELECT product, land_date, units FROM proj_po WHERE warehouse = 'intl' AND status = 'landed'"):
        receipts[("intl", prod)].append((ld, u))
    existing = {(loc, prod, as_of): src for loc, prod, as_of, src in
                conn.execute("SELECT location, product, as_of, source FROM proj_demand_snapshot WHERE location = ANY(%s) AND as_of >= %s", (list(LOCATIONS), FLOOR))}
    return counts, units, latest, receipts, existing


UPSERT = ("INSERT INTO proj_demand_snapshot (location, product, as_of, on_hand, allocated, source) VALUES (%s, %s, %s, %s, %s, %s) "
          "ON CONFLICT (location, product, as_of) DO UPDATE SET on_hand = EXCLUDED.on_hand, allocated = EXCLUDED.allocated, source = EXCLUDED.source, loaded_at = now() "
          "WHERE proj_demand_snapshot.source = '" + EST_SOURCE + "'")


def run(conn, today: dt.date, dry_run: bool = False) -> dict:
    counts, units, latest, receipts, existing = load_inputs(conn)
    rows, stats = compute(counts, units, latest, receipts, existing, today)
    stats["rows"] = len(rows)
    if not dry_run and rows:
        with conn.cursor() as cur:
            cur.executemany(UPSERT, rows)
        conn.commit()
    stats["window"] = f"{FLOOR.isoformat()}..{today.isoformat()}"
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    with db.JobRun("inventory_snapshots") as run_:
        with db.connect() as conn:
            today = pacific_today()
            run_.stats = run(conn, today, a.dry_run)
        run_.rows_written = 0 if a.dry_run else run_.stats["rows"]
        run_.data_through = today
        db.log(f"  {run_.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
