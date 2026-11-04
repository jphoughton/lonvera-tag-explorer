"""Creative Tracker -> tracker_rows (ALL products) + ad_tags(tag_set='tracker', version='tracker-live').
Works from a snapshot dir or LIVE from the sheet. Upserts keyed on row_key; rows missing from the sheet are never deleted (counted in stats).
Usage: python -m etl.tracker_sync [--snapshot-dir D]"""
from __future__ import annotations

import argparse
import collections

from . import db, sheets_io

NONE = "(none)"
TRACKER_VERSION = "tracker-live"


def family_key(ad_family: str, concept_id: str) -> str:
    f = (ad_family or "").strip().lower()
    return f if f else "id:" + (concept_id or "").strip().lower()


def parse_rows(values: list[list], product_by_name: dict[str, str]) -> tuple[list[dict], dict]:
    """Turn raw tracker values into row dicts (in sheet order) + parse stats."""
    header = values[0]
    hx = sheets_io.header_index(header)
    icp_col = next(v for k, v in hx.items() if k.startswith("ICP -"))
    col = lambda name: hx.get(name)
    stats = {"unknown_products": collections.Counter(), "bad_launch_dates": 0}
    seen = collections.Counter()
    taken: dict[str, tuple] = {}               # row_key -> (concept, family) that owns it
    out = []

    def g(r, name_or_idx):
        i = name_or_idx if isinstance(name_or_idx, int) else col(name_or_idx)
        if i is None:
            return ""
        v = sheets_io.cell(r, i, "")
        return str(v) if v is not None else ""

    for n, r in enumerate(values[1:], start=2):
        concept = g(r, "Concept ID")
        family = g(r, "Ad Family")
        k = (concept, family)
        seen[k] += 1
        row_key = f"{concept}#{seen[k]}"
        if taken.get(row_key, k) != k:         # same concept under a different Ad Family already owns this key: disambiguate
            row_key = f"{concept}#{seen[k]}~{family}"
        taken[row_key] = k
        prod_name = g(r, "Product").strip()
        slug = product_by_name.get(prod_name) or product_by_name.get(prod_name.lower())
        if not slug:
            slug = "unassigned"
            if prod_name:
                stats["unknown_products"][prod_name] += 1
        ld_raw = g(r, "Launch Date")
        ld = sheets_io.parse_date(ld_raw)
        if ld_raw.strip() and ld is None:
            stats["bad_launch_dates"] += 1
        raw = {hk: str(sheets_io.cell(r, hi, "")) for hk, hi in hx.items()}      # every column by (stripped) header; repeated headers get '#2'
        out.append({
            "slug": slug, "row_key": row_key, "concept_id": concept, "ad_family": family,
            "family_key": family_key(family, concept), "sheet_row": n, "status": g(r, "Status") or None,
            "ad_type": g(r, "Type") or None, "style": g(r, "Style") or None, "vendor": g(r, "Vendor") or None,
            "fidelity": g(r, "Creative Fidelity") or None, "proof": g(r, "Proof Mechanism") or None, "launch_date": ld,
            "concept_name": g(r, "Concept Name") or None, "description": g(r, "Short Description") or None,
            "hypothesis": g(r, "Hypothesis - Why are we testing this?") or None, "meta_ad_name": g(r, "Name") or None,
            "primary_text": [t for t in (g(r, "Primary Text"), g(r, "Primary Text 2"), g(r, "Primary Text 3")) if t.strip()],
            "headlines": [t for t in (g(r, "Ad Headline 1"), g(r, "Ad Headline 2"), g(r, "Ad Headline 3")) if t.strip()],
            "ad_description": g(r, "Ad Description") or None, "transcript": g(r, "Transcript") or None,
            "asset_url": g(r, "Asset URL") or None,
            "tracker_cluster": g(r, "Market Cluster") or None, "tracker_icp": g(r, icp_col) or None,
            "tracker_icp_legacy": g(r, "ICP (legacy)") or None, "tracker_pain": g(r, "Pain Point") or None,
            "tracker_emotion": g(r, "Emotion") or None, "tracker_ingredient": g(r, "Lead Ingredient") or None,
            "raw": raw,
        })
    stats["unknown_products"] = dict(stats["unknown_products"])
    return out, stats


UPSERT = """
INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, style, vendor, fidelity, proof,
  launch_date, concept_name, description, hypothesis, meta_ad_name, primary_text, headlines, ad_description, transcript, asset_url,
  tracker_cluster, tracker_icp, tracker_icp_legacy, tracker_pain, tracker_emotion, tracker_ingredient, raw, synced_at)
VALUES (%(product_id)s, %(row_key)s, %(concept_id)s, %(ad_family)s, %(family_key)s, %(sheet_row)s, %(status)s, %(ad_type)s, %(style)s, %(vendor)s,
  %(fidelity)s, %(proof)s, %(launch_date)s, %(concept_name)s, %(description)s, %(hypothesis)s, %(meta_ad_name)s, %(primary_text)s, %(headlines)s,
  %(ad_description)s, %(transcript)s, %(asset_url)s, %(tracker_cluster)s, %(tracker_icp)s, %(tracker_icp_legacy)s, %(tracker_pain)s,
  %(tracker_emotion)s, %(tracker_ingredient)s, %(raw)s, now())
ON CONFLICT (row_key) DO UPDATE SET product_id=EXCLUDED.product_id, concept_id=EXCLUDED.concept_id, ad_family=EXCLUDED.ad_family,
  family_key=EXCLUDED.family_key, sheet_row=EXCLUDED.sheet_row, status=EXCLUDED.status, ad_type=EXCLUDED.ad_type, style=EXCLUDED.style,
  vendor=EXCLUDED.vendor, fidelity=EXCLUDED.fidelity, proof=EXCLUDED.proof, launch_date=EXCLUDED.launch_date,
  concept_name=EXCLUDED.concept_name, description=EXCLUDED.description, hypothesis=EXCLUDED.hypothesis, meta_ad_name=EXCLUDED.meta_ad_name,
  primary_text=EXCLUDED.primary_text, headlines=EXCLUDED.headlines, ad_description=EXCLUDED.ad_description, transcript=EXCLUDED.transcript,
  asset_url=EXCLUDED.asset_url, tracker_cluster=EXCLUDED.tracker_cluster, tracker_icp=EXCLUDED.tracker_icp,
  tracker_icp_legacy=EXCLUDED.tracker_icp_legacy, tracker_pain=EXCLUDED.tracker_pain, tracker_emotion=EXCLUDED.tracker_emotion,
  tracker_ingredient=EXCLUDED.tracker_ingredient, raw=EXCLUDED.raw, synced_at=now()
"""

TAG_UPSERT = """
INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, cluster, icp, pain_point, emotional_trigger, tagger, tagged_at)
VALUES (%s, 'tracker', %s, true, %s, %s, %s, %s, 'tracker', now())
ON CONFLICT (tracker_row_id, tag_set) WHERE is_current DO UPDATE SET version=EXCLUDED.version, cluster=EXCLUDED.cluster, icp=EXCLUDED.icp,
  pain_point=EXCLUDED.pain_point, emotional_trigger=EXCLUDED.emotional_trigger, tagged_at=now()
"""


def product_name_map(conn) -> tuple[dict, dict]:
    by_name, ids = {}, {}
    for pid, slug, names in conn.execute("SELECT id, slug, tracker_names FROM products").fetchall():
        ids[slug] = pid
        for n in names:
            by_name[n] = slug
            by_name[n.lower()] = slug
    return by_name, ids


def sync(conn, values: list[list]) -> dict:
    by_name, ids = product_name_map(conn)
    rows, stats = parse_rows(values, by_name)
    keys = [r["row_key"] for r in rows]
    assert len(keys) == len(set(keys)), "row_key collision"
    before = conn.execute("SELECT count(*) FROM tracker_rows").fetchone()[0]
    params = []
    for r in rows:
        p = dict(r)
        p["product_id"] = ids[r["slug"]]
        p["raw"] = db.jsonb(r["raw"])
        params.append(p)
    with conn.cursor() as cur:
        cur.executemany(UPSERT, params)
    idmap = dict(conn.execute("SELECT row_key, id FROM tracker_rows").fetchall())
    tags = []
    for r in rows:
        tags.append((idmap[r["row_key"]], TRACKER_VERSION, r["tracker_cluster"] or NONE, r["tracker_icp"] or NONE,
                     r["tracker_pain"] or NONE, r["tracker_emotion"]))
    with conn.cursor() as cur:
        cur.executemany(TAG_UPSERT, tags)
    after = conn.execute("SELECT count(*) FROM tracker_rows").fetchone()[0]
    missing = conn.execute("SELECT count(*) FROM tracker_rows WHERE NOT (row_key = ANY(%s))", (keys,)).fetchone()[0]
    per_prod = collections.Counter(r["slug"] for r in rows)
    stats.update({"rows_in_sheet": len(rows), "rows_before": before, "rows_after": after, "inserted": after - before,
                  "missing_from_sheet_kept": missing, "by_product": dict(per_prod),
                  "unique_family_keys": len({(r["slug"], r["family_key"]) for r in rows})})
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir")
    a = ap.parse_args()
    values = sheets_io.read_tab("creative_tracker", a.snapshot_dir)
    with db.JobRun("tracker_sync") as run, db.connect() as conn:
        stats = sync(conn, values)
        conn.commit()
        run.stats = stats
        run.rows_written = stats["rows_in_sheet"]
        db.log(f"  tracker rows in sheet {stats['rows_in_sheet']} (before {stats['rows_before']}, after {stats['rows_after']}), missing-kept {stats['missing_from_sheet_kept']}")
        db.log(f"  by product {stats['by_product']}; unknown products {stats['unknown_products']}; bad launch dates {stats['bad_launch_dates']}")


if __name__ == "__main__":
    main()
