"""Rebuild the committed line catalogs (etl/autotag/catalogs/<slug>.json) from the taxonomy_lines table, which etl.load_taxonomy fills from Josh's
Communication Overview sheet. Manual step after Josh edits the Overview: run `python -m etl.load_taxonomy` first (reads the sheet), then this, then commit.
Default is a dry run that prints what changed against the committed file; --write replaces the files. Line codes in the DB are the authority, so a line
that exists in the sheet but not yet in the catalog becomes taggable as soon as the new file is deployed.
Usage: python -m etl.autotag.refresh_catalog [--product slug] [--write]"""
from __future__ import annotations

import argparse
import json

from .. import db
from ..auto_tag import HERE, PRODUCTS


def build(conn, slug: str) -> list[dict]:
    ing = {}
    for lid, name in conn.execute("SELECT line_id, ingredient FROM line_ingredients ORDER BY line_id, sort_order, id").fetchall():
        if name:
            ing.setdefault(lid, []).append(name)
    rows = conn.execute("""SELECT l.id, l.line_code, l.cluster, l.icp, l.pain_point, l.emotional_trigger, l.story FROM taxonomy_lines l
                           JOIN products p ON p.id = l.product_id WHERE p.slug=%s ORDER BY l.sort_order, l.id""", (slug,)).fetchall()
    out = []
    for lid, code, cl, icp, pain, trig, story in rows:
        d = {"line": code, "cluster": cl, "icp": icp, "pain": pain, "trigger": trig or "", "story": story or ""}
        if ing.get(lid):
            d["ingredient"] = " + ".join(ing[lid])
        out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", choices=PRODUCTS)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    with db.connect() as conn:
        for slug in ((a.product,) if a.product else PRODUCTS):
            new = build(conn, slug)
            path = HERE / "catalogs" / f"{slug}.json"
            old = json.loads(path.read_text()) if path.exists() else []
            o, n = {c["line"]: c for c in old}, {c["line"]: c for c in new}
            added, removed = sorted(set(n) - set(o)), sorted(set(o) - set(n))
            changed = sorted(k for k in set(o) & set(n) if any(o[k].get(f) != n[k].get(f) for f in ("cluster", "icp", "pain", "trigger")))
            db.log(f"{slug}: {len(old)} -> {len(new)} lines; added {added}, removed {removed}, key changed {changed}")
            if a.write and new:
                path.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n")
                db.log(f"  wrote {path}")


if __name__ == "__main__":
    main()
