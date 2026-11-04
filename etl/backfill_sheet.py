"""Backfill from the Creative Key sheet (snapshot dir or live): Meta Ads - Daily -> ad_daily (family grain, source='sheet'),
Meta Ads - Lifetime -> ad_lifetime / meta_ad_lifetime / meta_ads, Meta Ads - Windows A->B -> meta_family_map.
--mapping sheet|extended (default sheet). Prints the dollar delta between the two modes.
Usage: python -m etl.backfill_sheet --snapshot-dir D [--mapping sheet|extended] [--as-of 2026-11-02] [--no-delta]"""
from __future__ import annotations

import argparse
import datetime as dt

from . import build_metrics, db, sheets_io
from .resolver import Resolver, fam
from .sheets_io import num, serial_to_date

LIFETIME_CUTOFF = "2026-09-17"


def parse_daily(rows, as_of: dt.date | None = None):
    out = []
    for r in rows[2:]:
        if len(r) < 3 or isinstance(r[0], bool) or not isinstance(r[0], (int, float)):
            continue
        day = serial_to_date(r[0])
        if as_of and day > as_of:
            continue
        name = str(r[1]).strip()
        if not name:
            continue
        out.append((day, name, num(r[2]), num(sheets_io.cell(r, 3, 0)), num(sheets_io.cell(r, 4, 0)), num(sheets_io.cell(r, 6, 0))))
    return out


def load_daily(conn, daily) -> dict:
    """Replace source='sheet' rows for every sheet day that does not already have API (ad-grain) rows."""
    api_days = {r[0] for r in conn.execute("SELECT DISTINCT day FROM ad_daily WHERE source <> 'sheet'").fetchall()}
    sheet_days = sorted({d[0] for d in daily})
    load_days = [d for d in sheet_days if d not in api_days]
    conn.execute("DELETE FROM ad_daily WHERE source = 'sheet' AND day = ANY(%s)", (load_days,))
    ld = set(load_days)
    rows = [("fam:" + name.lower(), day, name.lower(), name, "", sp, pu, cv, cl, "sheet")
            for day, name, sp, pu, cv, cl in daily if day in ld]
    n = db.bulk_copy(conn, "ad_daily", ["ad_id", "day", "meta_family_key", "meta_family", "campaign_id", "spend", "purchases", "conv_value", "link_clicks", "source"], rows)
    return {"sheet_days": len(sheet_days), "loaded_days": len(load_days), "skipped_api_days": len(sheet_days) - len(load_days), "rows": n,
            "first_day": sheet_days[0].isoformat(), "last_day": sheet_days[-1].isoformat()}


def load_lifetime(conn, rows) -> dict:
    fam_sum: dict[str, list] = {}
    ads = []
    for r in rows[2:]:
        if len(r) < 3 or not str(r[0]).strip():
            continue
        tkey = str(r[0]).lower()
        s = fam_sum.setdefault(tkey, [0.0, 0.0, 0.0])
        sp, pu, cv = num(sheets_io.cell(r, 6, 0)), num(sheets_io.cell(r, 7, 0)), num(sheets_io.cell(r, 8, 0))
        s[0] += sp; s[1] += pu; s[2] += cv
        ad_id = str(r[2]).strip()
        if ad_id:
            name = str(r[1])
            ads.append((ad_id, str(sheets_io.cell(r, 3, "")) or None, name, fam(name).lower(), serial_to_date(sheets_io.cell(r, 4, "")),
                        str(sheets_io.cell(r, 5, "")) or None, tkey, sp, pu, cv))
    conn.execute("TRUNCATE ad_lifetime")
    db.execmany(conn, "INSERT INTO ad_lifetime (family_key, spend, purchases, conv_value) VALUES (%s,%s,%s,%s)",
                [(k, round(v[0], 2), round(v[1], 2), round(v[2], 2)) for k, v in fam_sum.items()])
    db.execmany(conn, """INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, created_date, status) VALUES (%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (ad_id) DO UPDATE SET account=EXCLUDED.account, ad_name=EXCLUDED.ad_name, meta_family_key=EXCLUDED.meta_family_key,
                         created_date=coalesce(EXCLUDED.created_date, meta_ads.created_date), status=coalesce(EXCLUDED.status, meta_ads.status)""",
                [a[:6] for a in ads])
    db.execmany(conn, """INSERT INTO meta_ad_lifetime (ad_id, meta_family_key, tracker_family_key, spend, purchases, conv_value) VALUES (%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (ad_id) DO UPDATE SET meta_family_key=EXCLUDED.meta_family_key, tracker_family_key=EXCLUDED.tracker_family_key,
                         spend=EXCLUDED.spend, purchases=EXCLUDED.purchases, conv_value=EXCLUDED.conv_value""",
                [(a[0], a[3], a[6], round(a[7], 2), round(a[8], 2), round(a[9], 2)) for a in ads])
    created = {}
    for a in ads:
        if a[4] and (a[3] not in created or a[4].isoformat() < created[a[3]]):
            created[a[3]] = a[4].isoformat()
    return {"families": len(fam_sum), "ads": len(ads), "spend": round(sum(v[0] for v in fam_sum.values()), 2), "_created": created}


def windows_map(rows) -> dict[str, tuple[str, str | None]]:
    """Windows tab A (Meta family) -> B (tracker family), keys lower-cased. Blank B -> None (unmatched)."""
    out = {}
    for r in rows[2:]:
        if not r or not str(r[0]).strip():
            continue
        a = str(r[0]).strip()
        b = str(r[1]).strip().lower() if len(r) > 1 and str(r[1]).strip() else None
        out[a.lower()] = (a, b)
    return out


def build_maps(conn, daily, win_rows, created) -> tuple[list, list, dict]:
    """-> (sheet_rows, extended_rows, info). Row = (meta_family_key, meta_family, tracker_family_key, rule)."""
    wm = windows_map(win_rows)
    daily_fams = {}
    for _, name, *_ in daily:
        daily_fams.setdefault(name.lower(), name)
    sheet_rows = {k: (k, a, b, "sheet-windows" if b else None) for k, (a, b) in wm.items()}
    for k, name in daily_fams.items():
        if k not in sheet_rows:
            sheet_rows[k] = (k, name, None, None)
    ext = dict(sheet_rows)
    trows = [{"family": f, "concept": c, "adname": n or ""} for f, c, n in
             conn.execute("SELECT ad_family, concept_id, meta_ad_name FROM tracker_rows").fetchall()]
    res = Resolver(trows, created)
    tracker_keys = {f.strip().lower() for f, in conn.execute("SELECT DISTINCT ad_family FROM tracker_rows WHERE ad_family <> ''").fetchall()}
    n_new = 0
    for k, name in daily_fams.items():
        if k in wm:
            continue                                   # present in Windows: the sheet's own resolver output stands
        tf, rule = res.resolve_rule(name)
        if tf and tf.strip().lower() in tracker_keys:
            ext[k] = (k, name, tf.strip().lower(), rule)
            n_new += 1
    return list(sheet_rows.values()), list(ext.values()), {"windows_families": len(wm), "daily_families": len(daily_fams),
                                                            "absent_from_windows": sum(1 for k in daily_fams if k not in wm),
                                                            "resolver_added": n_new}


def write_map(conn, rows):
    conn.execute("TRUNCATE meta_family_map")
    db.bulk_copy(conn, "meta_family_map", ["meta_family_key", "meta_family", "tracker_family_key", "rule"], rows)


def delta_report(a: dict, b: dict, label_a="sheet", label_b="extended"):
    lines = [f"{'product':<12}{'launch$ ' + label_a:>18}{'launch$ ' + label_b:>18}{'delta':>14}{'7d$ ' + label_a:>16}{'7d$ ' + label_b:>16}{'delta':>12}"]
    prods = sorted(set(a["by_product"]) | set(b["by_product"]))
    tot = [0.0] * 6
    for p in prods:
        x = a["by_product"].get(p, {}); y = b["by_product"].get(p, {})
        v = [x.get("spend_launch", 0), y.get("spend_launch", 0), x.get("spend_7d", 0), y.get("spend_7d", 0)]
        lines.append(f"{p:<12}{v[0]:>18,.2f}{v[1]:>18,.2f}{v[1] - v[0]:>14,.2f}{v[2]:>16,.2f}{v[3]:>16,.2f}{v[3] - v[2]:>12,.2f}")
        tot = [tot[0] + v[0], tot[1] + v[1], tot[2] + v[1] - v[0], tot[3] + v[2], tot[4] + v[3], tot[5] + v[3] - v[2]]
    lines.append(f"{'ALL':<12}{tot[0]:>18,.2f}{tot[1]:>18,.2f}{tot[2]:>14,.2f}{tot[3]:>16,.2f}{tot[4]:>16,.2f}{tot[5]:>12,.2f}")
    um = sorted(set(a["unmatched_by_product"]) | set(b["unmatched_by_product"]))
    lines.append("unmatched Meta 7d spend per product (sheet -> extended): " + "; ".join(
        f"{p} {a['unmatched_by_product'].get(p, {}).get('spend_7d', 0):,.2f} -> {b['unmatched_by_product'].get(p, {}).get('spend_7d', 0):,.2f}" for p in um))
    return "\n".join(lines)


def run(conn, snapshot_dir, mapping="extended", as_of=None, delta=True) -> dict:
    daily = parse_daily(sheets_io.read_tab("meta_daily", snapshot_dir), as_of)
    life_rows = sheets_io.read_tab("meta_lifetime", snapshot_dir)
    win_rows = sheets_io.read_tab("meta_windows", snapshot_dir)
    stats = {"daily": load_daily(conn, daily)}
    life = load_lifetime(conn, life_rows)
    created = life.pop("_created")
    stats["lifetime"] = life
    db.set_meta(conn, "daily_start", min(d[0] for d in daily).isoformat(), only_if_missing=True)
    db.set_meta(conn, "lifetime_cutoff", LIFETIME_CUTOFF, only_if_missing=True)
    sheet_rows, ext_rows, info = build_maps(conn, daily, win_rows, created)
    stats["mapping"] = {**info, "mode": mapping}
    chosen = sheet_rows if mapping == "sheet" else ext_rows
    if delta:
        write_map(conn, sheet_rows); s_stats = build_metrics.rebuild(conn, commit=False)
        write_map(conn, ext_rows); e_stats = build_metrics.rebuild(conn, commit=False)
        stats["delta_report"] = delta_report(s_stats, e_stats)
    write_map(conn, chosen)
    stats["metrics"] = build_metrics.rebuild(conn, commit=False)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir")
    ap.add_argument("--mapping", choices=["sheet", "extended"], default="extended")
    ap.add_argument("--as-of", help="ignore sheet Daily rows after this date (YYYY-MM-DD)")
    ap.add_argument("--no-delta", action="store_true")
    a = ap.parse_args()
    with db.JobRun("backfill") as run_, db.connect() as conn:
        s = run(conn, a.snapshot_dir, a.mapping, dt.date.fromisoformat(a.as_of) if a.as_of else None, not a.no_delta)
        conn.commit()
        run_.stats = {k: v for k, v in s.items() if k != "metrics"}
        run_.stats["metrics"] = {k: v for k, v in s["metrics"].items() if k != "by_product"}
        run_.rows_written = s["daily"]["rows"]
        run_.data_through = dt.date.fromisoformat(s["metrics"]["data_through"])
        db.log(f"  daily: {s['daily']}"); db.log(f"  lifetime: {s['lifetime']}"); db.log(f"  mapping: {s['mapping']}")
        if "delta_report" in s:
            db.log("\n=== mapping-mode delta: 'sheet' vs 'extended' (Resolver for families absent from Windows) ===\n" + s["delta_report"])
        for p, v in s["metrics"]["by_product"].items():
            db.log(f"  [{a.mapping}] {p}: {v}")


if __name__ == "__main__":
    main()
