"""Stage totals per day (ad_daily x meta_campaigns.stage, all products) vs the sheet's `Meta Campaign - History` tab (must agree within 1% per day);
also family x day totals vs the Daily tab. Usage: python -m etl.verify_stage --snapshot-dir D"""
from __future__ import annotations

import argparse
import collections
import sys

from . import db, meta_pull, sheets_io
from .backfill_sheet import parse_daily


def famday_vs_sheet(conn, snapshot_dir) -> dict:
    sheet = {(d, n.lower()): [sp, pu, cv, cl] for d, n, sp, pu, cv, cl in parse_daily(sheets_io.read_tab("meta_daily", snapshot_dir))}
    mine = {(r[0], r[1]): [float(x) for x in r[2:]] for r in conn.execute(
        "SELECT day, meta_family_key, sum(spend), sum(purchases), sum(conv_value), sum(link_clicks) FROM ad_daily GROUP BY 1,2").fetchall()}
    per_day = meta_pull.compare_famday(mine, sheet)
    worst = max(per_day.items(), key=lambda kv: abs(kv[1]["api"] - kv[1]["sheet"]))
    api_days = {r[0] for r in conn.execute("SELECT DISTINCT day FROM ad_daily WHERE source='meta_api'").fetchall()}
    tot_s = sum(x["sheet"] for x in per_day.values()); tot_a = sum(x["api"] for x in per_day.values())
    return {"days": len(per_day), "api_days": len(api_days), "sheet_total": round(tot_s, 2), "db_total": round(tot_a, 2), "diff": round(tot_a - tot_s, 2),
            "worst_day": worst[0].isoformat(), "worst_day_diff": round(worst[1]["api"] - worst[1]["sheet"], 2),
            "days_with_diff_over_$1": [(d.isoformat(), round(x["api"] - x["sheet"], 2)) for d, x in per_day.items() if abs(x["api"] - x["sheet"]) > 1],
            "max_family_diff": round(max(x["max_fam_diff"] for x in per_day.values()), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir", required=True)
    a = ap.parse_args()
    with db.connect() as conn:
        fd = famday_vs_sheet(conn, a.snapshot_dir)
        st = meta_pull.reconcile_stage(conn, a.snapshot_dir)
    print("family x day vs Daily tab:", fd)
    print("stage vs Meta Campaign - History:", st)
    sys.exit(0 if st["n_beyond"] == 0 else 1)


if __name__ == "__main__":
    main()
