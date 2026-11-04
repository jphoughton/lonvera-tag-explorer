"""Communication Overview -> taxonomy_lines + line_ingredients (all products).
Product blocks start where column A holds the product name; blank cells in A-D/H/I are merged-down (fill down); one line per
cluster x ICP x pain point; ingredient rows hang off it. Vita Bull line codes / cluster colors come from the 2026-11-03 tagging cycle
(catalog.json + co_live.json, codes L01..); Beef Liver codes come from the 2026-11-04 tagging cycle (catalog.json, codes B01..B63;
lines added to the sheet after that cycle, e.g. the GLP-1 Hair Shedder ICP, get the next free B code); other products get L01.. in
sheet order. Non-VB products use a fixed palette. A line that already exists (matched on cluster x ICP x pain) keeps its row but is
re-coded when the tagging catalog says so (two-phase rename, so a code shuffle cannot trip the unique constraint).
Usage: python -m etl.load_taxonomy [--snapshot-dir D] [--cycle-dir C] [--bl-cycle-dir C]"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import db, sheets_io

DEFAULT_CYCLE = Path("/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-03-vb-linemap")
BL_CYCLE = Path("/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-04-bl-linemap")
CODE_PREFIX = {"beef-liver": "B"}                    # every other product: "L"
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
    """{(cluster, icp, pain): line_code} from a tagging cycle's catalog.json (the catalog the tagger used = code authority)."""
    cat = json.loads((cycle_dir / "catalog.json").read_text())
    return {(x["cluster"], x["icp"], x["pain"]): x["line"] for x in cat}


def vb_catalog(cycle_dir: Path):
    codes = catalog_codes(cycle_dir)
    co = json.loads((cycle_dir / "co_live.json").read_text())
    colors = {k.split("|")[0]: _hex(v) for k, v in co["colors"].items() if k.endswith("|cluster")}
    return codes, colors


def plan_codes(slug: str, lines: list[dict], existing: dict, catalog: dict) -> dict:
    """-> {(cluster, icp, pain): code}. Catalog code wins; else the existing code if it already carries the product prefix (and nobody else claimed it);
    else the next code after the highest number in use (catalog + kept codes), so new lines never reuse a number."""
    pre = code_prefix(slug)
    rx = re.compile(rf"{pre}(\d+)")
    taken: set[str] = set()
    plan: dict = {}
    for ln in lines:                                       # pass 1: catalog codes
        key = (ln["cluster"], ln["icp"], ln["pain"])
        c = catalog.get(key)
        if c and c not in taken and key not in plan:
            plan[key] = c
            taken.add(c)
    for ln in lines:                                       # pass 2a: lines outside the catalog keep an existing prefixed code
        key = (ln["cluster"], ln["icp"], ln["pain"])
        old = existing.get(key, (None, None))[1]
        if key not in plan and old and rx.fullmatch(old) and old not in taken:
            plan[key] = old
            taken.add(old)
    nums = [int(m.group(1)) for c in list(taken) + list(catalog.values()) if (m := rx.fullmatch(c))]
    nxt = max(nums + [0]) + 1                              # next number after everything in use (never reuses a catalog number)
    for ln in lines:                                       # pass 2b: the rest get new codes in sheet order
        key = (ln["cluster"], ln["icp"], ln["pain"])
        if key not in plan:
            plan[key] = "%s%02d" % (pre, nxt)
            taken.add(plan[key])
            nxt += 1
    return plan


def load(conn, rows: list[list], cycle_dir: Path = DEFAULT_CYCLE, bl_cycle_dir: Path | None = BL_CYCLE, only: set[str] | None = None) -> dict:
    parsed = parse_overview(rows)
    if only:
        parsed = {k: v for k, v in parsed.items() if k in only}
    prod_ids = {s: i for i, s in conn.execute("SELECT id, slug FROM products").fetchall()}
    vb_codes, vb_colors = vb_catalog(cycle_dir) if (cycle_dir / "catalog.json").exists() else ({}, {})
    other_codes = {}
    if bl_cycle_dir is not None and (bl_cycle_dir / "catalog.json").exists():
        other_codes["beef-liver"] = catalog_codes(bl_cycle_dir)
    stats = {"products": {}, "warnings": []}
    for slug, lines in parsed.items():
        pid = prod_ids.get(slug)
        if pid is None:
            stats["warnings"].append(f"product {slug} not in products table; skipped")
            continue
        existing = {(r[1], r[2], r[3]): (r[0], r[4]) for r in conn.execute(
            "SELECT id, cluster, icp, pain_point, line_code FROM taxonomy_lines WHERE product_id=%s", (pid,)).fetchall()}
        catalog = vb_codes if slug == "vita-bull" else other_codes.get(slug, {})
        plan = plan_codes(slug, lines, existing, catalog)
        # re-code existing lines whose code differs from the plan: two-phase (temp codes first) so a shuffle cannot hit UNIQUE (product_id, line_code)
        renames = [(existing[k][0], c) for k, c in plan.items() if k in existing and existing[k][1] != c]
        if renames:
            conn.execute("UPDATE taxonomy_lines SET line_code = 'tmp:' || id WHERE id = ANY(%s)", ([r[0] for r in renames],))
            for lid, code in renames:
                conn.execute("UPDATE taxonomy_lines SET line_code=%s WHERE id=%s", (code, lid))
        cl_order: dict[str, int] = {}
        for ln in lines:
            cl_order.setdefault(ln["cluster"], len(cl_order))
        seen_keys: set = set()
        n_ing = n_new = 0
        for pos, ln in enumerate(lines):
            key = (ln["cluster"], ln["icp"], ln["pain"])
            if key in seen_keys:                                  # same cluster/ICP/pain twice: merge ingredients into the first line
                stats["warnings"].append(f"{slug}: duplicate line {key}; ingredients appended to first occurrence")
                lid = existing_id
                for j, ing in enumerate(ln["ingredients"]):
                    conn.execute("INSERT INTO line_ingredients (line_id, ingredient, attacks, supports, sort_order) VALUES (%s,%s,%s,%s,%s)",
                                 (lid, ing["ingredient"], ing["attacks"], ing["supports"], 1000 + j))
                    n_ing += 1
                continue
            seen_keys.add(key)
            color = vb_colors.get(ln["cluster"]) if slug == "vita-bull" else None
            color = color or PALETTE[cl_order[ln["cluster"]] % len(PALETTE)]
            if key in existing:
                lid = existing[key][0]
                conn.execute("UPDATE taxonomy_lines SET emotional_trigger=%s, story=%s, cluster_color=%s, sort_order=%s WHERE id=%s",
                             (ln["trigger"], ln["story"], color, pos + 1, lid))
            else:
                lid = conn.execute(
                    "INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger, story, cluster_color, sort_order) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (pid, plan[key], ln["cluster"], ln["icp"], ln["pain"], ln["trigger"], ln["story"], color, pos + 1)).fetchone()[0]
                n_new += 1
            existing_id = lid
            conn.execute("DELETE FROM line_ingredients WHERE line_id=%s", (lid,))
            for j, ing in enumerate(ln["ingredients"]):
                conn.execute("INSERT INTO line_ingredients (line_id, ingredient, attacks, supports, sort_order) VALUES (%s,%s,%s,%s,%s)",
                             (lid, ing["ingredient"], ing["attacks"], ing["supports"], j))
                n_ing += 1
        # lines no longer on the sheet: keep (tags may reference them) but push to the end
        gone = [v[0] for k, v in existing.items() if k not in seen_keys]
        if gone:
            conn.execute("UPDATE taxonomy_lines SET sort_order = 9000 + id WHERE id = ANY(%s)", (gone,))
            stats["warnings"].append(f"{slug}: {len(gone)} line(s) no longer on the sheet (kept, sort_order>=9000)")
        not_in_catalog = [k for k in seen_keys if catalog and k not in catalog]
        stats["products"][slug] = {"lines": len(seen_keys), "clusters": len(cl_order), "ingredient_rows": n_ing, "recoded": len(renames),
                                   "inserted": n_new, "not_in_tagging_catalog": len(not_in_catalog)}
        if not_in_catalog:
            codes = sorted(plan[k] for k in not_in_catalog)
            stats["warnings"].append(f"{slug}: {len(not_in_catalog)} line(s) on the sheet are not in the tagging catalog; coded {codes}")
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
    ap.add_argument("--product", action="append", help="load only this product slug (repeatable); default all")
    a = ap.parse_args()
    rows = sheets_io.read_tab("communication_overview", a.snapshot_dir)
    with db.JobRun("load_taxonomy") as run, db.connect() as conn:
        stats = load(conn, rows, Path(a.cycle_dir), Path(a.bl_cycle_dir), set(a.product) if a.product else None)
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
