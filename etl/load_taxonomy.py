"""Communication Overview -> taxonomy_lines + line_ingredients (all products).
Product blocks start where column A holds the product name; blank cells in A-D/H/I are merged-down (fill down); one line per
cluster x ICP x pain point; ingredient rows hang off it. Vita Bull line codes / cluster colors come from the 2026-11-03 tagging cycle
(catalog.json + co_live.json); other products get L01.. in sheet order and a fixed palette.
Usage: python -m etl.load_taxonomy [--snapshot-dir D] [--cycle-dir C]"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import db, sheets_io

DEFAULT_CYCLE = Path("/Users/joshhoughton/Desktop/Google Sheets/lonvera_creative_key/cycles/c2026-11-03-vb-linemap")
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


def vb_catalog(cycle_dir: Path):
    cat = json.loads((cycle_dir / "catalog.json").read_text())
    co = json.loads((cycle_dir / "co_live.json").read_text())
    codes = {(x["cluster"], x["icp"], x["pain"]): x["line"] for x in cat}
    colors = {k.split("|")[0]: _hex(v) for k, v in co["colors"].items() if k.endswith("|cluster")}
    return codes, colors


def load(conn, rows: list[list], cycle_dir: Path = DEFAULT_CYCLE) -> dict:
    parsed = parse_overview(rows)
    prod_ids = {s: i for i, s in conn.execute("SELECT id, slug FROM products").fetchall()}
    vb_codes, vb_colors = vb_catalog(cycle_dir) if (cycle_dir / "catalog.json").exists() else ({}, {})
    stats = {"products": {}, "warnings": []}
    for slug, lines in parsed.items():
        pid = prod_ids.get(slug)
        if pid is None:
            stats["warnings"].append(f"product {slug} not in products table; skipped")
            continue
        existing = {(r[1], r[2], r[3]): (r[0], r[4]) for r in conn.execute(
            "SELECT id, cluster, icp, pain_point, line_code FROM taxonomy_lines WHERE product_id=%s", (pid,)).fetchall()}
        used_codes = {v[1] for v in existing.values()}
        if existing:
            nxt = max([int(c[1:]) for c in used_codes if re.fullmatch(r"L\d+", c)] + [0]) + 1
        else:
            nxt = 1
        cl_order: dict[str, int] = {}
        for ln in lines:
            cl_order.setdefault(ln["cluster"], len(cl_order))
        seen_keys: set = set()
        n_ing = 0
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
                lid, code = existing[key]
                conn.execute("UPDATE taxonomy_lines SET emotional_trigger=%s, story=%s, cluster_color=%s, sort_order=%s WHERE id=%s",
                             (ln["trigger"], ln["story"], color, pos + 1, lid))
            else:
                code = vb_codes.get(key) if slug == "vita-bull" else None
                if not code or code in used_codes:
                    code = "L%02d" % nxt
                    nxt += 1
                used_codes.add(code)
                lid = conn.execute(
                    "INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger, story, cluster_color, sort_order) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (pid, code, ln["cluster"], ln["icp"], ln["pain"], ln["trigger"], ln["story"], color, pos + 1)).fetchone()[0]
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
        stats["products"][slug] = {"lines": len(seen_keys), "clusters": len(cl_order), "ingredient_rows": n_ing}
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
    a = ap.parse_args()
    rows = sheets_io.read_tab("communication_overview", a.snapshot_dir)
    with db.JobRun("load_taxonomy") as run, db.connect() as conn:
        stats = load(conn, rows, Path(a.cycle_dir))
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
