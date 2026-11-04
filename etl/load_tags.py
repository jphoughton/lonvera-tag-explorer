"""Load a tagging cycle's results (e.g. tagmap_rows_v2.json, tagmap_rows.json) as ad_tags(tag_set, version) for one product.
Rows are matched to tracker_rows by (concept_id, family) + ordinal among rows sharing that pair (ordered by the file's `row`), never by sheet row.
The tracker keeps growing after a cycle, so the match is NOT strict: tracker rows without a tag stay '(untagged)' until a later cycle tags them, tags whose key
is no longer in the tracker are skipped (listed as a warning); the load fails only when the matched coverage of the product's real tracker rows is below
--min-coverage (default 0.90) or when the matches are inconsistent (the file's ad type disagrees with the tracker on more than 2% of matched rows).
`family` = the tracker's Ad Family, or the Concept ID when the family is blank (the Beef Liver export writes the concept id into blank families,
the tracker keeps them blank; both sides are normalised the same way).
Accepts both cycle file shapes: Vita Bull (`seen`, `method`, text_* copy-only fields) and Beef Liver (`saw` -> seen, `media` -> method,
`heard` / `gap` / `media_note` -> ad_tags.extra). A new version marks the previous current tags not-current; re-running the same version
replaces it (tracker rows the new file does not cover keep their current tag). After a linemap load that leaves >= min-coverage of the product's tracker
rows tagged, products.tag_set is flipped to the tag set (idempotent) and the coverage % is reported.
Also stores the blind re-tag agreement numbers behind the ad-detail reliability note in app_meta (`reliability:<slug>`).
Usage: python -m etl.load_tags --file F --tag-set linemap --version V --product vita-bull
       python -m etl.load_tags --file F --tag-set linemap --version linemap-v2-2026-11-04 --product beef-liver --qa-cycle-dir C
       python -m etl.load_tags --product vita-bull --reliability-only --vb-defaults"""
from __future__ import annotations

import argparse
import collections
import glob
import json
from datetime import date
from pathlib import Path

from . import db

# Vita Bull blind re-tag agreement (105 units, 2026-11-03 cycle): the numbers the API used to hard-code.
VB_RELIABILITY = {
    "n": 105,
    "overall": {"line": 0.79, "icp": 0.84, "cluster": 0.88},
    "by_confidence": {"high": {"line": 0.93}, "low": {"line": 0.52}},
    "source": "Vita Bull cycle c2026-11-03-vb-linemap, blind re-tag of 105 units (top-35 spenders + 70 random)",
}

EXTRA_KEYS = ("unit_id", "text_confidence", "text_icp", "text_cluster", "text_label", "cur_icp", "cur_legacy_icp", "cur_pain", "cur_emotion",
              "heard", "gap", "media_note", "media")


def load_file(path: str) -> list[dict]:
    return json.loads(open(path).read())


def normalize(t: dict) -> dict:
    """Canonical tag: seen <- seen | saw; method <- method | media; everything else as in the file."""
    n = dict(t)
    if n.get("seen") in (None, "") and t.get("saw") not in (None, ""):
        n["seen"] = t["saw"]
    elif "seen" not in n and "saw" in t:
        n["seen"] = t["saw"]
    if n.get("method") in (None, "") and t.get("media") not in (None, ""):
        n["method"] = t["media"]
    return n


def _fam(cid, af) -> tuple[str, str]:
    cid = str(cid).strip()
    return cid, (str(af).strip() if af is not None and str(af).strip() else cid)


def _live(conn) -> str:
    """' AND deleted_at IS NULL' once migration 007 (soft delete) is applied, else ''."""
    has = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name='tracker_rows' AND column_name='deleted_at'").fetchone()
    return " AND deleted_at IS NULL" if has else ""


# a tracker row with a blank Concept ID is a placeholder, not an ad: the tagging cycles skip it, so it is neither matched nor required
_REAL = " AND btrim(concept_id) <> ''"


def match_rows(conn, product_id: int, tags: list[dict]):
    """-> (matches [(tag, tracker_row_id)], problems dict). Placeholder rows (blank Concept ID) and soft-deleted rows are not part of the tracker side.
    problems: tags_without_tracker_row (keys of tags with no tracker row left), tracker_rows_without_tag (keys of tracker rows nobody tagged: new since the
    cycle), count_mismatch (key, tags, rows) for groups whose sizes differ. None of them is an error by itself; `load` decides."""
    by_key: dict[tuple, list] = collections.defaultdict(list)
    for tid, cid, af in conn.execute("SELECT id, concept_id, ad_family FROM tracker_rows WHERE product_id=%s" + _live(conn) + _REAL +
                                     " ORDER BY sheet_row NULLS LAST, id", (product_id,)).fetchall():
        by_key[_fam(cid, af)].append(tid)
    tag_by_key: dict[tuple, list] = collections.defaultdict(list)
    for t in sorted(tags, key=lambda t: t["row"]):
        tag_by_key[_fam(t["concept_id"], t.get("ad_family"))].append(t)
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


def _tag_row(t: dict, rid: int, tag_set: str, version: str, line_id) -> tuple:
    text_line = t.get("text_line")
    copy_only = text_line if (text_line and text_line != t.get("line")) else None
    extra = {k: t[k] for k in EXTRA_KEYS if k in t}
    extra["line_code"] = t.get("line")
    extra["tag_file_row"] = t["row"]
    return (rid, tag_set, version, line_id, t["cluster"], t["icp"], t["pain"], t.get("trigger"), t.get("confidence"),
            t.get("method"), t.get("seen"), t.get("why"), t.get("runner_up"), copy_only, version.split("-")[0], db.jsonb(extra))


def _fmt_keys(keys, n=20) -> list[str]:
    return [" | ".join(k) if isinstance(k, tuple) else str(k) for k in keys[:n]]


def load(conn, product_slug: str, tag_set: str, version: str, tags: list[dict], allow_mismatch: bool = False, flip: bool = True,
         min_coverage: float = 0.90) -> dict:
    pid = conn.execute("SELECT id FROM products WHERE slug=%s", (product_slug,)).fetchone()[0]
    tags = [normalize(t) for t in tags]
    tax = conn.execute("SELECT id, cluster, icp, pain_point, emotional_trigger, line_code FROM taxonomy_lines WHERE product_id=%s", (pid,)).fetchall()
    lines = {(c, i, p, t or ""): (lid, code) for lid, c, i, p, t, code in tax}                  # a line = cluster x ICP x pain x trigger (migration 006)
    by3: dict[tuple, list] = collections.defaultdict(list)
    for lid, c, i, p, t, code in tax:
        by3[(c, i, p)].append((lid, code))
    matches, problems = match_rows(conn, pid, tags)
    n_real = conn.execute("SELECT count(*) FROM tracker_rows WHERE product_id=%s" + _live(conn) + _REAL, (pid,)).fetchone()[0]
    coverage = len(matches) / n_real if n_real else 0.0
    if coverage < min_coverage and not allow_mismatch:
        raise RuntimeError(f"tag file does not match the tracker: {len(matches)} of {n_real} tracker rows matched ({coverage:.1%}) < min coverage {min_coverage:.0%}; "
                           f"{ {k: _fmt_keys(v, 5) if k != 'count_mismatch' else v[:5] for k, v in problems.items() if v} }")
    ids = [m[1] for m in matches]
    rows, missing_lines, code_mismatch = [], collections.Counter(), collections.Counter()
    for t, rid in matches:
        lk = (t["cluster"], t["icp"], t["pain"], t.get("trigger") or "")
        ln = lines.get(lk)
        if ln is None and len(by3.get(lk[:3], [])) == 1:                                       # trigger edited since the cycle: the only line with this key
            ln = by3[lk[:3]][0]
        if ln is None:
            missing_lines[lk] += 1
        elif t.get("line") and t["line"] != ln[1]:
            code_mismatch[(t["line"], ln[1])] += 1
        rows.append(_tag_row(t, rid, tag_set, version, ln[0] if ln else None))
    if missing_lines:
        raise RuntimeError(f"tag lines missing from taxonomy: {dict(missing_lines)}")
    if code_mismatch and not allow_mismatch:
        raise RuntimeError(f"tag file line codes disagree with taxonomy_lines (tag code, taxonomy code): {dict(code_mismatch)}")
    # informational: does the tracker still look like it did when the cycle ran?
    info = {"blank_family_rows": 0, "type_mismatch": 0, "status_mismatch": 0, "sheet_row_differs": 0}
    if ids:
        trk = {r[0]: r for r in conn.execute("SELECT id, ad_family, ad_type, status, sheet_row FROM tracker_rows WHERE id = ANY(%s)", (ids,)).fetchall()}
        for t, rid in matches:
            _, af, ty, st, sr = trk[rid]
            info["blank_family_rows"] += not (af or "").strip()
            info["type_mismatch"] += bool(t.get("type")) and (ty or "") != t["type"]
            info["status_mismatch"] += bool(t.get("status")) and (st or "") != t["status"]
            info["sheet_row_differs"] += sr != t["row"]
    if info["type_mismatch"] > 0.02 * max(1, len(matches)) and not allow_mismatch:       # matched pairs that disagree on VID/IMG: the file is not this tracker's
        raise RuntimeError(f"tag file is inconsistent with the tracker: ad type differs on {info['type_mismatch']} of {len(matches)} matched rows")
    placeholders = conn.execute("SELECT count(*) FROM tracker_rows WHERE product_id=%s" + _live(conn) + " AND btrim(concept_id) = ''", (pid,)).fetchone()[0]
    with conn.transaction():
        prod_rows = [r[0] for r in conn.execute("SELECT id FROM tracker_rows WHERE product_id=%s" + _live(conn) + _REAL, (pid,)).fetchall()]
        all_prod_rows = [r[0] for r in conn.execute("SELECT id FROM tracker_rows WHERE product_id=%s", (pid,)).fetchall()]
        # a row that changed product in the tracker can still carry the old product's line-map tag: retire any current tag whose line belongs to another product
        foreign = conn.execute("""UPDATE ad_tags SET is_current=false WHERE tag_set=%s AND is_current AND tracker_row_id = ANY(%s) AND line_id IS NOT NULL
                                  AND line_id NOT IN (SELECT id FROM taxonomy_lines WHERE product_id=%s)""", (tag_set, all_prod_rows, pid)).rowcount
        # only the rows this file tags move to the new version; rows it does not cover keep whatever tag they have
        prev = conn.execute("UPDATE ad_tags SET is_current=false WHERE tag_set=%s AND is_current AND tracker_row_id = ANY(%s) AND version <> %s",
                            (tag_set, ids, version)).rowcount
        conn.execute("DELETE FROM ad_tags WHERE tag_set=%s AND version=%s AND tracker_row_id = ANY(%s)", (tag_set, version, all_prod_rows))
        db.execmany(conn, """INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger,
                             confidence, method, seen, why, runner_up, copy_only_line, tagger, extra)
                             VALUES (%s,%s,%s,true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
        covered = conn.execute("SELECT count(*) FROM ad_tags WHERE tag_set=%s AND is_current AND tracker_row_id = ANY(%s)", (tag_set, prod_rows)).fetchone()[0]
        tagged_pct = covered / len(prod_rows) if prod_rows else 0.0
        flipped = None
        if flip and tag_set == "linemap":
            if tagged_pct >= min_coverage:
                flipped = conn.execute("UPDATE products SET tag_set=%s WHERE id=%s AND tag_set <> %s", (tag_set, pid, tag_set)).rowcount
            else:
                flipped = f"skipped: {covered} of {len(prod_rows)} tracker rows tagged ({tagged_pct:.1%} < {min_coverage:.0%})"
    used = collections.Counter(t.get("line") for t, _ in matches)
    return {"product": product_slug, "tag_set": tag_set, "version": version, "tags_in_file": len(tags), "matched": len(matches),
            "tracker_rows": len(prod_rows), "tagged_rows": covered, "coverage_pct": round(100 * coverage, 1), "tagged_pct": round(100 * tagged_pct, 1),
            "tracker_rows_untagged": len(prod_rows) - covered, "untagged_keys": _fmt_keys(problems["tracker_rows_without_tag"]),
            "tags_skipped_not_in_tracker": len(problems["tags_without_tracker_row"]), "skipped_tag_keys": _fmt_keys(problems["tags_without_tracker_row"]),
            "unique_families": len({r[0] for r in conn.execute(
                "SELECT family_key FROM tracker_rows WHERE id = ANY(%s)", (ids,)).fetchall()}),
            "previous_marked_not_current": prev, "foreign_line_tags_retired": foreign, "ignored_blank_concept_rows": placeholders, "problems": {k: len(v) for k, v in problems.items()}, "line_code_mismatch": sum(code_mismatch.values()),
            "confidence": dict(collections.Counter(t.get("confidence") for t, _ in matches)),
            "method": dict(collections.Counter(t.get("method") for t, _ in matches)),
            "lines_used": len([c for c in used if c]), "lines_in_taxonomy": len(tax),
            "unused_line_codes": sorted(v[1] for v in lines.values() if v[1] not in used),
            "tracker_vs_file": info, "product_tag_set_flipped": flipped}


# ---- blind re-tag agreement (reliability note) ---------------------------------------------------------------------------------------

def compute_reliability(cycle_dir: Path) -> dict:
    """Agreement between the first tags (results/*.json) and the blind QA re-tags (qa_results/*.json) of a unit-based cycle, overall and by the
    first tag's confidence. Units, not ads: each unit counts once. Needs catalog.json (line -> cluster / ICP / pain)."""
    cat = {c["line"]: c for c in json.loads((cycle_dir / "catalog.json").read_text())}
    res = {r["unit_id"]: r for f in sorted(glob.glob(str(cycle_dir / "results" / "*.json"))) for r in json.loads(Path(f).read_text())}
    qa = {r["unit_id"]: r for f in sorted(glob.glob(str(cycle_dir / "qa_results" / "*.json"))) for r in json.loads(Path(f).read_text())}
    keys = ("line", "icp", "cluster", "pain")
    tot, agree = collections.Counter(), {k: collections.Counter() for k in keys}
    for u, q in qa.items():
        a = res[u]
        for grp in ("ALL", a["confidence"]):
            tot[grp] += 1
            A, Q = cat[a["line"]], cat[q["line"]]
            agree["line"][grp] += a["line"] == q["line"]
            agree["icp"][grp] += A["icp"] == Q["icp"]
            agree["cluster"][grp] += A["cluster"] == Q["cluster"]
            agree["pain"][grp] += A["pain"] == Q["pain"]
    rate = lambda grp: {"n": tot[grp], **{k: round(agree[k][grp] / tot[grp], 4) for k in keys}}
    out = {"n": tot["ALL"], "overall": {k: v for k, v in rate("ALL").items() if k != "n"},
           "by_confidence": {c: rate(c) for c in ("high", "medium", "low") if tot[c]},
           "source": f"{cycle_dir.name}: blind re-tag of {tot['ALL']} units (image + video samples), agreement with the first tag by the first tag's confidence",
           "computed": date.today().isoformat()}
    return out


def store_reliability(conn, product_slug: str, stats: dict) -> None:
    db.set_meta(conn, f"reliability:{product_slug}", json.dumps(stats, sort_keys=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--tag-set", default="linemap")
    ap.add_argument("--version")
    ap.add_argument("--product", required=True)
    ap.add_argument("--allow-mismatch", action="store_true", help="do not fail on low coverage / inconsistent matches / line-code disagreements")
    ap.add_argument("--min-coverage", type=float, default=0.90, help="fail below this share of the product's tracker rows matched; flip tag_set at or above it (default 0.90)")
    ap.add_argument("--no-flip", action="store_true", help="do not set products.tag_set to this tag set after a full-coverage load")
    ap.add_argument("--qa-cycle-dir", help="compute blind re-tag agreement from this unit-based cycle dir and store app_meta reliability:<product>")
    ap.add_argument("--vb-defaults", action="store_true", help="store the Vita Bull blind re-tag numbers as reliability:<product>")
    ap.add_argument("--reliability-only", action="store_true", help="skip the tag load, only (re)store the reliability numbers")
    a = ap.parse_args()
    if not a.reliability_only and not (a.file and a.version):
        ap.error("--file and --version are required unless --reliability-only")
    with db.JobRun("load_tags") as run, db.connect() as conn:
        stats: dict = {}
        if not a.reliability_only:
            stats = load(conn, a.product, a.tag_set, a.version, load_file(a.file), a.allow_mismatch, flip=not a.no_flip, min_coverage=a.min_coverage)
            run.rows_written = stats["matched"]
            db.log(f"  {a.product}: matched {stats['matched']} of {stats['tracker_rows']} tracker rows ({stats['coverage_pct']}%), "
                   f"{stats['tracker_rows_untagged']} untagged, {stats['tags_skipped_not_in_tracker']} tag(s) skipped (key not in tracker), "
                   f"tagged {stats['tagged_pct']}% after load, tag_set flip: {stats['product_tag_set_flipped']}")
            if stats["skipped_tag_keys"]:
                db.log(f"  WARN tags skipped, not in the tracker: {stats['skipped_tag_keys']}")
        rel = None
        if a.qa_cycle_dir:
            rel = compute_reliability(Path(a.qa_cycle_dir))
        elif a.vb_defaults:
            rel = VB_RELIABILITY
        if rel:
            store_reliability(conn, a.product, rel)
            stats["reliability"] = rel
        conn.commit()
        run.stats = stats
        db.log(f"  {stats}")


if __name__ == "__main__":
    main()
