"""Creative Tracker -> tracker_rows (ALL products) + ad_tags(tag_set='tracker', version='tracker-live').
Works from a snapshot dir or LIVE from the sheet. Hardened (QA round 1):
  * required headers (Concept ID, Ad Family, Product, Status, Launch Date, ICP) must exist or the sync is REFUSED with no writes (a renamed 'Ad Family' header
    used to blank every family and zero all spend);
  * refuses (no writes) when the blank-Ad-Family count jumps >20% (and by >=3) vs the previous sync, or the row count drops >20%; --force overrides;
  * rows with a blank Concept ID are placeholders: skipped (counted in stats.skipped_blank_concept), and any already stored get deleted_at like any missing row;
  * rows the sheet no longer has get deleted_at (never removed); they are cleared again if the row reappears; deleted rows are ignored by build_metrics;
  * row identity: row_key = concept_id#n. For concepts that have several rows (or several DB rows) rows are matched to existing keys by a content
    fingerprint (Concept Name + Short Description + Hypothesis + Brief Date) before falling back to order, so a row inserted above does not shift tags.
Usage: python -m etl.tracker_sync [--snapshot-dir D] [--force]"""
from __future__ import annotations

import argparse
import collections
import hashlib

from . import db, sheets_io

NONE = "(none)"
TRACKER_VERSION = "tracker-live"
REQUIRED_HEADERS = ["Concept ID", "Ad Family", "Product", "Status", "Launch Date"]
MAX_JUMP = 0.20


class TrackerRefused(db.JobFailed):
    """The sync was refused before any write (bad headers / suspicious change)."""


def family_key(ad_family: str, concept_id: str) -> str:
    f = (ad_family or "").strip().lower()
    return f if f else "id:" + (concept_id or "").strip().lower()


def fingerprint(concept_name: str, description: str, hypothesis: str, brief_date: str) -> str:
    parts = [(x or "").strip().lower() for x in (concept_name, description, hypothesis, brief_date)]
    return hashlib.md5("\x1f".join(parts).encode()).hexdigest() if any(parts) else ""


def parse_rows(values: list[list], product_by_name: dict[str, str]) -> tuple[list[dict], dict]:
    """Turn raw tracker values into row dicts (in sheet order, no row_key yet) + parse stats. Raises TrackerRefused on missing headers."""
    if not values:
        raise TrackerRefused("tracker sync refused: the Creative Tracker tab is empty")
    header = values[0]
    hx = sheets_io.header_index(header)
    missing = [h for h in REQUIRED_HEADERS if h not in hx] + ([] if any(k.startswith("ICP -") for k in hx) else ["ICP - Target Avatar ..."])
    if missing:
        raise TrackerRefused(f"tracker sync refused: required header(s) missing from the Creative Tracker: {missing}")
    icp_col = next(v for k, v in hx.items() if k.startswith("ICP -"))
    col = lambda name: hx.get(name)
    stats = {"unknown_products": collections.Counter(), "bad_launch_dates": 0, "skipped_blank_concept": 0}
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
        if not concept.strip():                              # placeholder rows (blank Concept ID, e.g. '3 - Briefing' stubs) are not creatives
            stats["skipped_blank_concept"] += 1
            continue
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
            "slug": slug, "concept_id": concept, "ad_family": family,
            "family_key": family_key(family, concept), "sheet_row": n, "status": g(r, "Status") or None,
            "ad_type": g(r, "Type") or None, "style": g(r, "Style") or None, "vendor": g(r, "Vendor") or None,
            "fidelity": g(r, "Creative Fidelity") or None, "proof": g(r, "Proof Mechanism") or None, "launch_date": ld,
            "concept_name": g(r, "Concept Name") or None, "description": g(r, "Short Description") or None,
            "hypothesis": g(r, "Hypothesis - Why are we testing this?") or None, "meta_ad_name": g(r, "Name") or None,
            "fingerprint": fingerprint(g(r, "Concept Name"), g(r, "Short Description"), g(r, "Hypothesis - Why are we testing this?"), g(r, "Brief Date")),
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


def assign_keys(rows: list[dict], existing: list[dict]) -> dict:
    """Give every sheet row its row_key, reusing existing keys. existing: dicts with row_key, concept_id, ad_family, fingerprint (all DB rows, deleted included).
    Mutates rows ('row_key') and returns stats {matched_by_fingerprint, matched_by_order, new}."""
    by_db = collections.defaultdict(list)
    for e in existing:
        by_db[e["concept_id"]].append(e)
    by_sheet = collections.defaultdict(list)
    for r in rows:
        by_sheet[r["concept_id"]].append(r)
    taken = {e["row_key"] for e in existing}
    claimed: set[str] = set()
    st = {"matched_by_fingerprint": 0, "matched_by_order": 0, "new": 0}

    def claim(r, e, how):
        r["row_key"] = e["row_key"]
        claimed.add(e["row_key"])
        st[how] += 1

    for concept, srows in by_sheet.items():
        drows = list(by_db.get(concept, []))
        if len(srows) == 1 and len(drows) == 1:
            claim(srows[0], drows[0], "matched_by_order")
            continue
        free_s = list(srows)
        free_d = list(drows)
        for phase in ("family+fp", "fp", "family"):
            for r in list(free_s):
                for e in free_d:
                    fp_ok = bool(r["fingerprint"]) and r["fingerprint"] == (e.get("fingerprint") or "")
                    if (phase == "family+fp" and fp_ok and r["ad_family"] == e["ad_family"]) or (phase == "fp" and fp_ok) or \
                            (phase == "family" and r["ad_family"] == e["ad_family"]):          # leftovers: an edited row keeps its key
                        claim(r, e, "matched_by_fingerprint" if phase != "family" else "matched_by_order")
                        free_s.remove(r)
                        free_d.remove(e)
                        break
        for r in free_s:                                   # genuinely new rows: smallest unused ordinal
            n = 1
            while f"{concept}#{n}" in taken or f"{concept}#{n}" in claimed:
                n += 1
            r["row_key"] = f"{concept}#{n}"
            claimed.add(r["row_key"])
            st["new"] += 1
    return st


UPSERT = """
INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, style, vendor, fidelity, proof,
  launch_date, concept_name, description, hypothesis, meta_ad_name, primary_text, headlines, ad_description, transcript, asset_url,
  tracker_cluster, tracker_icp, tracker_icp_legacy, tracker_pain, tracker_emotion, tracker_ingredient, raw, fingerprint, deleted_at, synced_at)
VALUES (%(product_id)s, %(row_key)s, %(concept_id)s, %(ad_family)s, %(family_key)s, %(sheet_row)s, %(status)s, %(ad_type)s, %(style)s, %(vendor)s,
  %(fidelity)s, %(proof)s, %(launch_date)s, %(concept_name)s, %(description)s, %(hypothesis)s, %(meta_ad_name)s, %(primary_text)s, %(headlines)s,
  %(ad_description)s, %(transcript)s, %(asset_url)s, %(tracker_cluster)s, %(tracker_icp)s, %(tracker_icp_legacy)s, %(tracker_pain)s,
  %(tracker_emotion)s, %(tracker_ingredient)s, %(raw)s, %(fingerprint)s, NULL, now())
ON CONFLICT (row_key) DO UPDATE SET product_id=EXCLUDED.product_id, concept_id=EXCLUDED.concept_id, ad_family=EXCLUDED.ad_family,
  family_key=EXCLUDED.family_key, sheet_row=EXCLUDED.sheet_row, status=EXCLUDED.status, ad_type=EXCLUDED.ad_type, style=EXCLUDED.style,
  vendor=EXCLUDED.vendor, fidelity=EXCLUDED.fidelity, proof=EXCLUDED.proof, launch_date=EXCLUDED.launch_date,
  concept_name=EXCLUDED.concept_name, description=EXCLUDED.description, hypothesis=EXCLUDED.hypothesis, meta_ad_name=EXCLUDED.meta_ad_name,
  primary_text=EXCLUDED.primary_text, headlines=EXCLUDED.headlines, ad_description=EXCLUDED.ad_description, transcript=EXCLUDED.transcript,
  asset_url=EXCLUDED.asset_url, tracker_cluster=EXCLUDED.tracker_cluster, tracker_icp=EXCLUDED.tracker_icp,
  tracker_icp_legacy=EXCLUDED.tracker_icp_legacy, tracker_pain=EXCLUDED.tracker_pain, tracker_emotion=EXCLUDED.tracker_emotion,
  tracker_ingredient=EXCLUDED.tracker_ingredient, raw=EXCLUDED.raw, fingerprint=EXCLUDED.fingerprint, deleted_at=NULL, synced_at=now()
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


def check_guards(conn, rows: list[dict], force: bool = False) -> dict:
    """Refuse (raise TrackerRefused, no writes) on a suspicious sheet. Returns the numbers compared."""
    blank = sum(1 for r in rows if not r["ad_family"].strip())
    prev_blank = db.get_meta(conn, "tracker_blank_family_count")
    prev_rows = db.get_meta(conn, "tracker_row_count")
    info = {"blank_family": blank, "prev_blank_family": int(prev_blank) if prev_blank is not None else None,
            "rows": len(rows), "prev_rows": int(prev_rows) if prev_rows is not None else None}
    if force:
        return info
    if prev_blank is not None and blank > int(prev_blank) * (1 + MAX_JUMP) and blank - int(prev_blank) >= 3:
        raise TrackerRefused(f"tracker sync refused: blank Ad Family rows jumped from {prev_blank} to {blank} (>{MAX_JUMP:.0%}); check the 'Ad Family' column. Re-run with --force if this is real.")
    if prev_rows is not None and int(prev_rows) >= 100 and len(rows) < int(prev_rows) * (1 - MAX_JUMP):
        raise TrackerRefused(f"tracker sync refused: row count fell from {prev_rows} to {len(rows)} (>{MAX_JUMP:.0%}). Re-run with --force if this is real.")
    return info


def sync(conn, values: list[list], force: bool = False) -> dict:
    by_name, ids = product_name_map(conn)
    rows, stats = parse_rows(values, by_name)                       # raises TrackerRefused on bad headers
    stats["guards"] = check_guards(conn, rows, force)               # raises TrackerRefused on suspicious jumps
    existing = [dict(row_key=a, concept_id=b, ad_family=c, fingerprint=d) for a, b, c, d in
                conn.execute("SELECT row_key, concept_id, ad_family, fingerprint FROM tracker_rows").fetchall()]
    stats["keys"] = assign_keys(rows, existing)
    keys = [r["row_key"] for r in rows]
    assert len(keys) == len(set(keys)), "row_key collision"
    before = conn.execute("SELECT count(*) FROM tracker_rows").fetchone()[0]
    was_deleted = conn.execute("SELECT count(*) FROM tracker_rows WHERE deleted_at IS NOT NULL AND row_key = ANY(%s)", (keys,)).fetchone()[0]
    params = []
    for r in rows:
        p = dict(r)
        p["product_id"] = ids[r["slug"]]
        p["raw"] = db.jsonb(r["raw"])
        params.append(p)
    with conn.cursor() as cur:
        cur.executemany(UPSERT, params)
    newly_deleted = conn.execute("UPDATE tracker_rows SET deleted_at = now() WHERE deleted_at IS NULL AND NOT (row_key = ANY(%s))", (keys,)).rowcount
    idmap = dict(conn.execute("SELECT row_key, id FROM tracker_rows").fetchall())
    tags = [(idmap[r["row_key"]], TRACKER_VERSION, r["tracker_cluster"] or NONE, r["tracker_icp"] or NONE, r["tracker_pain"] or NONE, r["tracker_emotion"]) for r in rows]
    with conn.cursor() as cur:
        cur.executemany(TAG_UPSERT, tags)
    after = conn.execute("SELECT count(*) FROM tracker_rows").fetchone()[0]
    per_prod = collections.Counter(r["slug"] for r in rows)
    db.set_meta(conn, "tracker_blank_family_count", str(stats["guards"]["blank_family"]))
    db.set_meta(conn, "tracker_row_count", str(len(rows)))
    stats.update({"rows_in_sheet": len(rows), "rows_before": before, "rows_after": after, "inserted": after - before,
                  "deleted_now": newly_deleted, "resurrected": was_deleted, "missing_from_sheet_kept": newly_deleted,
                  "by_product": dict(per_prod), "unique_family_keys": len({(r["slug"], r["family_key"]) for r in rows})})
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir")
    ap.add_argument("--force", action="store_true", help="skip the blank-family / row-count jump guards")
    a = ap.parse_args()
    values = sheets_io.read_tab("creative_tracker", a.snapshot_dir)
    with db.JobRun("tracker_sync") as run, db.connect() as conn:
        stats = sync(conn, values, a.force)
        conn.commit()
        run.stats = stats
        run.rows_written = stats["rows_in_sheet"]
        db.log(f"  tracker rows in sheet {stats['rows_in_sheet']} (before {stats['rows_before']}, after {stats['rows_after']}), newly deleted {stats['deleted_now']}, resurrected {stats['resurrected']}")
        db.log(f"  keys {stats['keys']}; by product {stats['by_product']}; unknown products {stats['unknown_products']}; bad launch dates {stats['bad_launch_dates']}")


if __name__ == "__main__":
    main()
