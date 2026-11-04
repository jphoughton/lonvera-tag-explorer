"""Load a tagging cycle's results (e.g. tagmap_rows_v2.json) as ad_tags(tag_set, version) for one product.
Rows are matched to tracker_rows by (concept_id, ad_family) + ordinal among rows sharing that pair (ordered by the file's `row`), never by sheet row.
A new version marks the previous current tags not-current; re-running the same version replaces it.
Usage: python -m etl.load_tags --file F --tag-set linemap --version V --product vita-bull"""
from __future__ import annotations

import argparse
import collections
import json

from . import db


def load_file(path: str) -> list[dict]:
    return json.loads(open(path).read())


def match_rows(conn, product_id: int, tags: list[dict]):
    """-> (matches [(tag, tracker_row_id)], problems dict). Multisets of (concept_id, ad_family) must be equal."""
    by_key: dict[tuple, list] = collections.defaultdict(list)
    for tid, cid, af in conn.execute("SELECT id, concept_id, ad_family FROM tracker_rows WHERE product_id=%s ORDER BY sheet_row NULLS LAST, id",
                                     (product_id,)).fetchall():
        by_key[(cid, af)].append(tid)
    tag_by_key: dict[tuple, list] = collections.defaultdict(list)
    for t in sorted(tags, key=lambda t: t["row"]):
        tag_by_key[(str(t["concept_id"]), str(t["ad_family"]))].append(t)
    problems = {"tags_without_tracker_row": [], "tracker_rows_without_tag": [], "count_mismatch": []}
    matches = []
    for k, tl in tag_by_key.items():
        rl = by_key.get(k, [])
        if len(rl) != len(tl):
            problems["count_mismatch"].append((k, len(tl), len(rl)))
        for i, t in enumerate(tl):
            if i < len(rl):
                matches.append((t, rl[i]))
            else:
                problems["tags_without_tracker_row"].append(k)
    for k, rl in by_key.items():
        if len(rl) > len(tag_by_key.get(k, [])):
            problems["tracker_rows_without_tag"] += [k] * (len(rl) - len(tag_by_key.get(k, [])))
    return matches, problems


def load(conn, product_slug: str, tag_set: str, version: str, tags: list[dict], allow_mismatch: bool = False) -> dict:
    pid = conn.execute("SELECT id FROM products WHERE slug=%s", (product_slug,)).fetchone()[0]
    lines = {(c, i, p): (lid, code) for lid, c, i, p, code in conn.execute(
        "SELECT id, cluster, icp, pain_point, line_code FROM taxonomy_lines WHERE product_id=%s", (pid,)).fetchall()}
    matches, problems = match_rows(conn, pid, tags)
    bad = sum(len(v) for v in problems.values())
    if bad and not allow_mismatch:
        raise RuntimeError(f"tag file does not match the tracker: { {k: v[:5] for k, v in problems.items() if v} }")
    ids = [m[1] for m in matches]
    rows, missing_lines, code_mismatch = [], collections.Counter(), 0
    for t, rid in matches:
        lk = (t["cluster"], t["icp"], t["pain"])
        ln = lines.get(lk)
        if ln is None:
            missing_lines[lk] += 1
        elif t.get("line") and t["line"] != ln[1]:
            code_mismatch += 1
        text_line = t.get("text_line")
        copy_only = text_line if (text_line and text_line != t.get("line")) else None
        extra = {k: t.get(k) for k in ("unit_id", "text_confidence", "text_icp", "text_cluster", "text_label", "cur_icp", "cur_legacy_icp", "cur_pain", "cur_emotion") if k in t}
        extra["line_code"] = t.get("line")
        extra["tag_file_row"] = t["row"]
        rows.append((rid, tag_set, version, ln[0] if ln else None, t["cluster"], t["icp"], t["pain"], t.get("trigger"), t.get("confidence"),
                     t.get("method"), t.get("seen"), t.get("why"), t.get("runner_up"), copy_only, version.split("-")[0], db.jsonb(extra)))
    if missing_lines:
        raise RuntimeError(f"tag lines missing from taxonomy: {dict(missing_lines)}")
    with conn.transaction():
        prod_rows = [r[0] for r in conn.execute("SELECT id FROM tracker_rows WHERE product_id=%s", (pid,)).fetchall()]
        prev = conn.execute("UPDATE ad_tags SET is_current=false WHERE tag_set=%s AND is_current AND tracker_row_id = ANY(%s) AND version <> %s",
                            (tag_set, prod_rows, version)).rowcount
        conn.execute("DELETE FROM ad_tags WHERE tag_set=%s AND version=%s AND tracker_row_id = ANY(%s)", (tag_set, version, prod_rows))
        db.execmany(conn, """INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger,
                             confidence, method, seen, why, runner_up, copy_only_line, tagger, extra)
                             VALUES (%s,%s,%s,true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    return {"product": product_slug, "tag_set": tag_set, "version": version, "tags_in_file": len(tags), "matched": len(matches),
            "previous_marked_not_current": prev, "problems": {k: len(v) for k, v in problems.items()}, "line_code_mismatch": code_mismatch,
            "confidence": dict(collections.Counter(t.get("confidence") for t, _ in matches))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--tag-set", default="linemap")
    ap.add_argument("--version", required=True)
    ap.add_argument("--product", required=True)
    ap.add_argument("--allow-mismatch", action="store_true")
    a = ap.parse_args()
    with db.JobRun("load_tags") as run, db.connect() as conn:
        stats = load(conn, a.product, a.tag_set, a.version, load_file(a.file), a.allow_mismatch)
        conn.commit()
        run.stats = stats
        run.rows_written = stats["matched"]
        db.log(f"  {stats}")


if __name__ == "__main__":
    main()
