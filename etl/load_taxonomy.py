"""Communication Overview -> taxonomy_lines + line_ingredients (all products).
Product blocks start where column A holds the product name; blank cells in A-D/H/I are merged-down (fill down); one line per
cluster x ICP x pain point; ingredient rows hang off it. Vita Bull line codes / cluster colors come from the 2026-11-03 tagging cycle
(catalog.json + co_live.json, codes L01..); Beef Liver codes come from the 2026-11-04 tagging cycle (catalog.json, codes B01..B63;
lines added to the sheet after that cycle, e.g. the GLP-1 Hair Shedder ICP, get the next free B code); Boneflex codes (BF01..BF54) come from the
2026-11-05 cycle; other products get L01.. in sheet order. Non-VB products use a fixed palette.
A line is cluster x ICP x pain point x emotional trigger (migration 006: two Boneflex lines share cluster + ICP + pain and differ by trigger).
A sheet line is matched to an existing row on the full key first, then on cluster x ICP x pain (a changed trigger keeps its row and code); a matched row
is re-coded when the tagging catalog says so (two-phase rename, so a code shuffle cannot trip UNIQUE (product_id, line_code)).
Usage: python -m etl.load_taxonomy [--snapshot-dir D] [--cycle-dir C] [--bl-cycle-dir C] [--bf-cycle-dir C] [--product slug]"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

from . import db, sheets_io

DEFAULT_CYCLE = Path("/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-03-vb-linemap")
BL_CYCLE = Path("/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-04-bl-linemap")
BF_CYCLE = Path("/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-05-bf-linemap")
CODE_PREFIX = {"beef-liver": "B", "boneflex": "BF"}  # every other product: "L"
PRODUCT_LABELS = {"vita bull": "vita-bull", "beef liver": "beef-liver", "boneflex": "boneflex",
                  "vita belle": "vita-belle", "vital flow": "vital-flow"}
# fixed pastel palette for products without sheet colors (cluster order -> color)
PALETTE = ["#CFE2F3", "#F4CCCC", "#FFE5B4", "#D0E7C3", "#FDD9A0", "#DAD2E9", "#D9EAD3", "#FCE5CD", "#C9DAF8", "#EAD1DC", "#D0E0E3", "#FFF2CC"]


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(round(float(c) * 255)) for c in rgb)


def parse_overview(rows: list[list]) -> dict[str, list[dict]]:
    """-> {product_slug: [line dicts in sheet order]}; each line: cluster, icp, pain, trigger, story, ingredients[{ingredient,attacks,supports}]."""
    out: dict[str, list[dict]] = {}
    cur_slug = cluster = icp = None
    line = None
    for r in rows[1:]:
        r = [str(c) for c in r] + [""] * (9 - len(r))
        a, b, c, d, e, f, g, h, i = [x.strip() for x in r[:9]]
        if a:
            cur_slug = PRODUCT_LABELS.get(a.lower())
            if cur_slug is None:
                raise ValueError(f"Unknown product label in Communication Overview col A: {a!r}")
            out.setdefault(cur_slug, [])
            cluster = icp = None
            line = None
        if cur_slug is None:
            continue
        if b:
            cluster = b
        if c:
            icp = c
        if d:                                              # a new pain-point line starts
            line = {"cluster": cluster, "icp": icp, "pain": d, "trigger": h or None, "story": i or None, "ingredients": []}
            out[cur_slug].append(line)
        if line is None:
            continue
        if e or f or g:
            line["ingredients"].append({"ingredient": e or None, "attacks": f or None, "supports": g or None})
    return out


def code_prefix(slug: str) -> str:
    return CODE_PREFIX.get(slug, "L")


def catalog_codes(cycle_dir: Path) -> dict:
    """{(cluster, icp, pain, trigger): line_code} from a tagging cycle's catalog.json (the catalog the tagger used = code authority)."""
    cat = json.loads((cycle_dir / "catalog.json").read_text())
    return {(x["cluster"], x["icp"], x["pain"], x.get("trigger") or ""): x["line"] for x in cat}


def lookup_code(catalog: dict, cluster: str, icp: str, pain: str, trigger: str | None) -> str | None:
    """Catalog code for a sheet line: the full key, else the only catalog line with this cluster x ICP x pain (a trigger edited since the cycle)."""
    code = catalog.get((cluster, icp, pain, trigger or ""))
    if code:
        return code
    cands = [v for k, v in catalog.items() if k[:3] == (cluster, icp, pain)]
    return cands[0] if len(cands) == 1 else None


def vb_catalog(cycle_dir: Path):
    codes = catalog_codes(cycle_dir)
    co = json.loads((cycle_dir / "co_live.json").read_text())
    colors = {k.split("|")[0]: _hex(v) for k, v in co["colors"].items() if k.endswith("|cluster")}
    return codes, colors


def plan_codes(slug: str, lines: list[dict], matched_codes: list, catalog: dict) -> list[str]:
    """-> one code per line (lines = distinct cluster x ICP x pain x trigger lines in sheet order; matched_codes = the code of the existing row each line
    was matched to, else None). Catalog code wins; else the matched row's code if it already carries the product prefix (and nobody else claimed it);
    else the next code after the highest number in use (catalog + kept codes), so new lines never reuse a number."""
    pre = code_prefix(slug)
    rx = re.compile(rf"{pre}(\d+)")
    taken: set[str] = set()
    plan: list = [None] * len(lines)
    for i, ln in enumerate(lines):                         # pass 1: catalog codes
        c = lookup_code(catalog, ln["cluster"], ln["icp"], ln["pain"], ln["trigger"])
        if c and c not in taken:
            plan[i] = c
            taken.add(c)
    for i, old in enumerate(matched_codes):                # pass 2a: lines outside the catalog keep an existing prefixed code
        if plan[i] is None and old and rx.fullmatch(old) and old not in taken:
            plan[i] = old
            taken.add(old)
    nums = [int(m.group(1)) for c in list(taken) + list(catalog.values()) if (m := rx.fullmatch(c))]
    nxt = max(nums + [0]) + 1                              # next number after everything in use (never reuses a catalog number)
    for i in range(len(lines)):                            # pass 2b: the rest get new codes in sheet order
        if plan[i] is None:
            plan[i] = "%s%02d" % (pre, nxt)
            taken.add(plan[i])
            nxt += 1
    return plan


def match_existing(lines: list[dict], existing: list[tuple]) -> list:
    """lines: distinct lines in sheet order; existing: [(id, cluster, icp, pain, trigger, code)] in sort order -> per line the matched existing row or None.
    Pass A: same cluster x ICP x pain x trigger. Pass B: same cluster x ICP x pain (first unused) - a trigger edited in the sheet keeps its row."""
    used: set = set()
    match: list = [None] * len(lines)
    for i, ln in enumerate(lines):
        for e in existing:
            if e[0] not in used and e[1:4] == (ln["cluster"], ln["icp"], ln["pain"]) and (e[4] or "") == (ln["trigger"] or ""):
                match[i] = e
                used.add(e[0])
                break
    for i, ln in enumerate(lines):
        if match[i] is None:
            for e in existing:
                if e[0] not in used and e[1:4] == (ln["cluster"], ln["icp"], ln["pain"]):
                    match[i] = e
                    used.add(e[0])
                    break
    return match


def load(conn, rows: list[list], cycle_dir: Path = DEFAULT_CYCLE, bl_cycle_dir: Path | None = BL_CYCLE, only: set[str] | None = None,
         bf_cycle_dir: Path | None = BF_CYCLE) -> dict:
    parsed = parse_overview(rows)
    if only:
        parsed = {k: v for k, v in parsed.items() if k in only}
    prod_ids = {s: i for i, s in conn.execute("SELECT id, slug FROM products").fetchall()}
    vb_codes, vb_colors = vb_catalog(cycle_dir) if (cycle_dir / "catalog.json").exists() else ({}, {})
    other_codes = {}
    for slug, d in (("beef-liver", bl_cycle_dir), ("boneflex", bf_cycle_dir)):
        if d is not None and (d / "catalog.json").exists():
            other_codes[slug] = catalog_codes(d)
    stats = {"products": {}, "warnings": []}
    for slug, sheet_lines in parsed.items():
        pid = prod_ids.get(slug)
        if pid is None:
            stats["warnings"].append(f"product {slug} not in products table; skipped")
            continue
        existing = [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in conn.execute(
            "SELECT id, cluster, icp, pain_point, emotional_trigger, line_code FROM taxonomy_lines WHERE product_id=%s ORDER BY sort_order, id", (pid,)).fetchall()]
        catalog = vb_codes if slug == "vita-bull" else other_codes.get(slug, {})
        # a line first inserted once the product already has line-map tags reads 0 ads by construction (shown as "added after tagging", not "untested")
        has_tags = bool(conn.execute("""SELECT EXISTS (SELECT 1 FROM ad_tags t JOIN tracker_rows r ON r.id = t.tracker_row_id
                                        WHERE r.product_id = %s AND t.tag_set = 'linemap' AND t.is_current)""", (pid,)).fetchone()[0])
        cl_order: dict[str, int] = {}
        for ln in sheet_lines:
            cl_order.setdefault(ln["cluster"], len(cl_order))
        # distinct lines (cluster x ICP x pain x trigger) in sheet order; a repeat of the full key folds its ingredients into the first occurrence
        firsts: list[dict] = []
        first_of: dict = {}
        n_ing = n_new = 0
        for pos, ln in enumerate(sheet_lines):
            key = (ln["cluster"], ln["icp"], ln["pain"], ln["trigger"] or "")
            if key in first_of:
                stats["warnings"].append(f"{slug}: duplicate line {key}; ingredients appended to first occurrence")
                first_of[key]["extra_ingredients"].extend(ln["ingredients"])
                continue
            first_of[key] = dict(ln, pos=pos, extra_ingredients=[])
            firsts.append(first_of[key])
        match = match_existing(firsts, existing)
        plan = plan_codes(slug, firsts, [m[5] if m else None for m in match], catalog)
        # re-code matched rows whose code differs from the plan: two-phase (temp codes first) so a shuffle cannot hit UNIQUE (product_id, line_code)
        renames = [(m[0], c) for m, c in zip(match, plan) if m and m[5] != c]
        if renames:
            conn.execute("UPDATE taxonomy_lines SET line_code = 'tmp:' || id WHERE id = ANY(%s)", ([r[0] for r in renames],))
            for lid, code in renames:
                conn.execute("UPDATE taxonomy_lines SET line_code=%s WHERE id=%s", (code, lid))
        for ln, m, code in zip(firsts, match, plan):
            color = vb_colors.get(ln["cluster"]) if slug == "vita-bull" else None
            color = color or PALETTE[cl_order[ln["cluster"]] % len(PALETTE)]
            if m:
                lid = m[0]
                conn.execute("UPDATE taxonomy_lines SET emotional_trigger=%s, story=%s, cluster_color=%s, sort_order=%s WHERE id=%s",
                             (ln["trigger"], ln["story"], color, ln["pos"] + 1, lid))
            else:
                lid = conn.execute(
                    "INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger, story, cluster_color, sort_order, added_after_tagging) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (pid, code, ln["cluster"], ln["icp"], ln["pain"], ln["trigger"], ln["story"], color, ln["pos"] + 1, has_tags)).fetchone()[0]
                n_new += 1
            conn.execute("DELETE FROM line_ingredients WHERE line_id=%s", (lid,))
            for j, ing in enumerate(ln["ingredients"]):
                conn.execute("INSERT INTO line_ingredients (line_id, ingredient, attacks, supports, sort_order) VALUES (%s,%s,%s,%s,%s)",
                             (lid, ing["ingredient"], ing["attacks"], ing["supports"], j))
                n_ing += 1
            for j, ing in enumerate(ln["extra_ingredients"]):
                conn.execute("INSERT INTO line_ingredients (line_id, ingredient, attacks, supports, sort_order) VALUES (%s,%s,%s,%s,%s)",
                             (lid, ing["ingredient"], ing["attacks"], ing["supports"], 1000 + j))
                n_ing += 1
        # lines no longer on the sheet: keep (tags may reference them) but push to the end
        matched_ids = {m[0] for m in match if m}
        gone = [e[0] for e in existing if e[0] not in matched_ids]
        if gone:
            conn.execute("UPDATE taxonomy_lines SET sort_order = 9000 + id WHERE id = ANY(%s)", (gone,))
            stats["warnings"].append(f"{slug}: {len(gone)} line(s) no longer on the sheet (kept, sort_order>=9000)")
        not_in_catalog = [i for i, ln in enumerate(firsts) if catalog and not lookup_code(catalog, ln["cluster"], ln["icp"], ln["pain"], ln["trigger"])]
        shared = collections.Counter((ln["cluster"], ln["icp"], ln["pain"]) for ln in firsts)
        stats["products"][slug] = {"lines": len(firsts), "clusters": len(cl_order), "ingredient_rows": n_ing, "recoded": len(renames),
                                   "inserted": n_new, "not_in_tagging_catalog": len(not_in_catalog),
                                   "lines_sharing_a_pain_point": sum(v for v in shared.values() if v > 1)}
        if not_in_catalog:
            stats["warnings"].append(f"{slug}: {len(not_in_catalog)} line(s) on the sheet are not in the tagging catalog; coded {sorted(plan[i] for i in not_in_catalog)}")
    return stats


def reconcile(conn, snapshot_dir) -> list[str]:
    """Compare Vita Bull ingredient rows with the overview_metrics snapshot tab (66 expected)."""
    msgs = []
    om = sheets_io.read_tab("overview_metrics", snapshot_dir)
    n_om = sum(1 for r in om[1:] if len(r) > 4 and str(r[4]).strip())
    n_db = conn.execute("SELECT count(*) FROM line_ingredients li JOIN taxonomy_lines t ON t.id=li.line_id "
                        "JOIN products p ON p.id=t.product_id WHERE p.slug='vita-bull'").fetchone()[0]
    n_lines = conn.execute("SELECT count(*) FROM taxonomy_lines t JOIN products p ON p.id=t.product_id WHERE p.slug='vita-bull'").fetchone()[0]
    msgs.append(f"Vita Bull: {n_lines} lines, {n_db} ingredient rows (overview_metrics tab has {n_om} ingredient rows) -> {'OK' if n_db == n_om else 'MISMATCH'}")
    return msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir")
    ap.add_argument("--cycle-dir", default=str(DEFAULT_CYCLE))
    ap.add_argument("--bl-cycle-dir", default=str(BL_CYCLE))
    ap.add_argument("--bf-cycle-dir", default=str(BF_CYCLE))
    ap.add_argument("--product", action="append", help="load only this product slug (repeatable); default all")
    a = ap.parse_args()
    rows = sheets_io.read_tab("communication_overview", a.snapshot_dir)
    with db.JobRun("load_taxonomy") as run, db.connect() as conn:
        stats = load(conn, rows, Path(a.cycle_dir), Path(a.bl_cycle_dir), set(a.product) if a.product else None, Path(a.bf_cycle_dir))
        conn.commit()
        run.stats = stats
        run.rows_written = sum(p["lines"] for p in stats["products"].values())
        if a.snapshot_dir:
            for m in reconcile(conn, a.snapshot_dir):
                db.log(m)
        for p, s in stats["products"].items():
            db.log(f"  {p}: {s}")
        for w in stats["warnings"]:
            db.log("  WARN", w)


if __name__ == "__main__":
    main()
