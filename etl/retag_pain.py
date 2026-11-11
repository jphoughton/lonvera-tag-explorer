"""Re-tag ads whose line tag picked the product MECHANISM instead of the PAIN the buyer feels.

Problem (audit 2026-11-10): ~1,700 tagged tracker rows carry a line whose pain point differs from the Creative Tracker's Pain Point although that pain point
exists on a line of the same product. This job asks the same headless Sonnet path as etl.auto_tag (new instructions: pain first, tracker pain is the default
answer) to tag each such row again, showing it the CURRENT tag as a possibly mistaken previous answer.

  python -m etl.retag_pain --select mismatch [--limit N] [--ids 158,160] [--out file.jsonl] [--workers 3]      # dry run: reads only, writes the --out file
  python -m etl.retag_pain --select mismatch --apply [--out file.jsonl] [--from file.jsonl]                    # writes ad_tags (one transaction per row)
  python -m etl.retag_pain --revert retag-pain-2026-11-10                                                      # restores the previous tags

Dry run: the database session is read-only (default_transaction_read_only), nothing is written but the --out JSON lines. Resumable: rows already in --out are
skipped. --apply --from FILE applies reviewed dry-run results without calling the model again. Apply: per row, in ONE transaction, the old current linemap tag
becomes is_current=false and the new tag (version retag-pain-YYYY-MM-DD, tagger retag-claude-sonnet, extra.previous_tag_id + reasons) is inserted; rows whose
new line equals the old line are skipped; a row that already has a tag of this version, or whose current tag is no longer the one the answer was made against,
is skipped (idempotent). --revert deletes the version's rows that are still current and makes the previous tags current again. Not part of the nightly run."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import random
import sys
import time

from . import auto_tag as at
from . import db

TAGGER = "retag-claude-sonnet"
VERSION_PREFIX = "retag-pain-"

SELECT_SQL = """
SELECT t.id, p.slug, t.concept_id, t.ad_family, t.family_key, t.ad_type, t.concept_name, t.description, t.hypothesis, t.meta_ad_name, t.primary_text,
       t.headlines, t.ad_description, t.transcript, t.tracker_cluster, t.tracker_icp, t.tracker_pain, t.tracker_emotion,
       (btrim(coalesce(t.raw->>'Brief Link', '')) <> '') AS briefed,
       g.id AS tag_id, g.line_id, g.pain_point, g.icp, g.cluster, g.emotional_trigger, g.confidence, g.why, g.seen, g.method, g.extra,
       ol.line_code AS old_line,
       coalesce((SELECT sum(m.spend_30d) FROM ad_metrics m WHERE m.family_key = t.family_key), 0) AS spend_30d,
       coalesce((SELECT sum(m.spend_launch) FROM ad_metrics m WHERE m.family_key = t.family_key), 0) AS spend_launch
FROM tracker_rows t
JOIN products p ON p.id = t.product_id
JOIN ad_tags g ON g.tracker_row_id = t.id AND g.tag_set = 'linemap' AND g.is_current
LEFT JOIN taxonomy_lines ol ON ol.id = g.line_id
WHERE t.deleted_at IS NULL AND btrim(coalesce(t.tracker_pain, '')) <> ''
  AND lower(regexp_replace(btrim(coalesce(g.pain_point, '')), '\\s+', ' ', 'g')) <> lower(regexp_replace(btrim(t.tracker_pain), '\\s+', ' ', 'g'))
  AND EXISTS (SELECT 1 FROM taxonomy_lines l WHERE l.product_id = t.product_id
              AND lower(regexp_replace(btrim(l.pain_point), '\\s+', ' ', 'g')) = lower(regexp_replace(btrim(t.tracker_pain), '\\s+', ' ', 'g')))
"""
COLS = ["id", "slug", "concept_id", "ad_family", "family_key", "ad_type", "concept_name", "description", "hypothesis", "meta_ad_name", "primary_text", "headlines",
        "ad_description", "transcript", "tracker_cluster", "tracker_icp", "tracker_pain", "tracker_emotion", "briefed", "tag_id", "line_id", "old_pain", "old_icp",
        "old_cluster", "old_trigger", "old_confidence", "old_why", "old_seen", "old_method", "old_extra", "old_line", "spend_30d", "spend_launch"]


def connect_ro() -> "db.psycopg.Connection":
    """A connection whose session cannot write (belt and braces for the dry run)."""
    conn = db.connect()
    conn.execute("SET default_transaction_read_only = on")
    conn.commit()
    return conn


def select_mismatch(conn, ids=None, limit: int | None = None, slugs=at.PRODUCTS) -> list[dict]:
    """Non-deleted tracker rows with a current linemap tag whose pain point differs from tracker_pain, where tracker_pain is a pain point on a line of the product.
    Biggest family spend_30d first."""
    sql = SELECT_SQL + " AND p.slug = ANY(%s)"
    params: list = [list(slugs)]
    if ids:
        sql += " AND t.id = ANY(%s)"
        params.append(list(ids))
    sql += " ORDER BY spend_30d DESC, t.id"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    rows = [dict(zip(COLS, r)) for r in conn.execute(sql, params).fetchall()]
    return [r for r in rows if at.norm(r["tracker_pain"]) not in at.PLACEHOLDER_PAINS]


def sample_ids(rows: list[dict], top: int, per_product: int, seed: int, must: tuple = ()) -> list[int]:
    """The `top` highest spend_30d rows plus `per_product` seeded-random rows per product from the rest (`must` ids are forced in, replacing random picks)."""
    ranked = sorted(rows, key=lambda r: (-float(r["spend_30d"]), r["id"]))
    chosen = [r["id"] for r in ranked[:top]]
    rng = random.Random(seed)
    for slug in at.PRODUCTS:
        pool = sorted(r["id"] for r in rows if r["slug"] == slug and r["id"] not in chosen)
        forced = [i for i in must if i in pool]
        rest = [i for i in pool if i not in forced]
        k = max(0, per_product - len(forced))
        chosen += forced + rng.sample(rest, min(k, len(rest)))
    return chosen


def previous_tag_context(r: dict) -> dict:
    ex = r.get("old_extra") or {}
    keep = {"line": r["old_line"], "cluster": r["old_cluster"], "icp": r["old_icp"], "pain_point": r["old_pain"], "emotional_trigger": r["old_trigger"],
            "why": at._clip(r.get("old_why"), 240), "what_it_saw": at._clip(r.get("old_seen"), 240), "what_it_heard": at._clip(ex.get("heard"), 240),
            "media_note": at._clip(ex.get("media_note") or ex.get("media"), 240), "gap": at._clip(ex.get("gap"), 240),
            "note": "an earlier pass chose this; it may have confused the product mechanism with the pain the buyer feels"}
    return {k: v for k, v in keep.items() if v}


def family_for(r: dict) -> dict:
    """The auto_tag 'family' dict for ONE tracker row (this job tags rows, not families)."""
    copy_len = sum(len(x or "") for x in (r["primary_text"] or [])) + sum(len(x or "") for x in (r["headlines"] or [])) + len(r["ad_description"] or "") + len(r["transcript"] or "")
    rep = at.make_rep(copy_len, r["concept_id"], r["ad_type"], r["concept_name"], r["meta_ad_name"], r["description"], r["hypothesis"], r["primary_text"], r["headlines"],
                      r["ad_description"], r["transcript"], r["tracker_cluster"], r["tracker_icp"], r["tracker_pain"], r["tracker_emotion"], r["briefed"])
    return {"family_key": r["family_key"], "rows": [r["id"]], "rep": rep}


def prepare(conn, r: dict) -> tuple[dict, tuple | None]:
    unit, img = at.gather(conn, family_for(r))
    unit["previous_tag"] = previous_tag_context(r)
    return unit, img


def result_record(r: dict, cat_by_code: dict, ans: dict | None, usage: dict | None, unit: dict | None, has_image: bool, error: str | None = None) -> dict:
    rec = {"id": r["id"], "concept_id": r["concept_id"], "product": r["slug"], "spend_30d": round(float(r["spend_30d"]), 2), "spend_launch": round(float(r["spend_launch"]), 2),
           "briefed": bool(r["briefed"]), "tracker_icp": r["tracker_icp"], "tracker_pain": r["tracker_pain"], "old_tag_id": r["tag_id"], "old_line": r["old_line"],
           "old_pain": r["old_pain"], "old_icp": r["old_icp"], "old_cluster": r["old_cluster"]}
    if error or ans is None:
        rec["error"] = error or "no answer"
        return rec
    c = cat_by_code[ans["line"]]
    is_video = bool(unit.get("_is_video"))
    conf = at.final_confidence(ans["confidence"], is_video, has_image)
    rec.update({"new_line": ans["line"], "new_cluster": c["cluster"], "new_icp": c["icp"], "new_pain": c["pain"], "new_trigger": c.get("trigger"),
                "confidence": conf, "model_confidence": ans["confidence"], "runner_up": ans.get("runner_up"), "kept_tracker_pain": ans.get("kept_tracker_pain"),
                "override_reason": ans.get("override_reason"), "why": ans.get("why"), "saw": ans.get("saw"),
                "method": at.method_for(is_video, has_image), "has_image": has_image, "is_video": is_video, "usage": usage})
    return rec


# ---- apply / revert ------------------------------------------------------------------------------------------------------------------

def apply_record(conn, rec: dict, version: str, line_ids: dict) -> str:
    """-> 'applied' | 'same_line' | 'exists' | 'stale' | 'error'. One transaction; idempotent."""
    if rec.get("error") or not rec.get("new_line"):
        return "error"
    if rec["new_line"] == rec.get("old_line"):
        return "same_line"
    lid = line_ids.get((rec["product"], rec["new_line"]))
    if not lid:
        return "error"
    with conn.transaction():
        if conn.execute("SELECT 1 FROM ad_tags WHERE tracker_row_id=%s AND tag_set='linemap' AND version=%s", (rec["id"], version)).fetchone():
            return "exists"
        cur = conn.execute("SELECT id FROM ad_tags WHERE tracker_row_id=%s AND tag_set='linemap' AND is_current FOR UPDATE", (rec["id"],)).fetchone()
        if not cur or (rec.get("old_tag_id") and cur[0] != rec["old_tag_id"]):
            return "stale"
        old_id = cur[0]
        conn.execute("UPDATE ad_tags SET is_current=false WHERE id=%s", (old_id,))
        extra = db.jsonb({"source": "retag-pain", "model": at.MODEL, "line_code": rec["new_line"], "previous_tag_id": old_id, "previous_line_code": rec.get("old_line"),
                          "previous_pain_point": rec.get("old_pain"), "kept_tracker_pain": rec.get("kept_tracker_pain"), "override_reason": rec.get("override_reason"),
                          "model_confidence": rec.get("model_confidence"), "has_image": rec.get("has_image"), "is_video": rec.get("is_video"),
                          "needs_review": rec.get("confidence") == "low"})
        conn.execute("""INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger, confidence, method,
                        seen, why, runner_up, tagger, extra) VALUES (%s,'linemap',%s,true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                     (rec["id"], version, lid, rec["new_cluster"], rec["new_icp"], rec["new_pain"], rec["new_trigger"], rec["confidence"], rec["method"],
                      rec.get("saw"), rec.get("why"), rec.get("runner_up"), TAGGER, extra))
    return "applied"


def revert(conn, version: str) -> dict:
    """Undo a retag version: per row, delete the version's tag if it is still current and make the previous tag current again."""
    if not version.startswith(VERSION_PREFIX):
        raise db.JobFailed(f"refusing to revert {version!r}: only {VERSION_PREFIX}* versions")
    out = {"reverted": 0, "not_current": 0, "no_previous": 0}
    rows = conn.execute("SELECT id, tracker_row_id, is_current, (extra->>'previous_tag_id')::bigint FROM ad_tags WHERE tag_set='linemap' AND version=%s ORDER BY id", (version,)).fetchall()
    for tid, rid, is_cur, prev in rows:
        with conn.transaction():
            if not is_cur:
                out["not_current"] += 1
                continue
            if not prev or not conn.execute("SELECT 1 FROM ad_tags WHERE id=%s AND tracker_row_id=%s", (prev, rid)).fetchone():
                out["no_previous"] += 1
                continue
            conn.execute("DELETE FROM ad_tags WHERE id=%s", (tid,))
            conn.execute("UPDATE ad_tags SET is_current=true WHERE id=%s", (prev,))
            out["reverted"] += 1
    return out


# ---- run -----------------------------------------------------------------------------------------------------------------------------

def read_done(path: str | None) -> dict[int, dict]:
    done: dict[int, dict] = {}
    if path:
        try:
            with open(path) as fh:
                for ln in fh:
                    if ln.strip():
                        d = json.loads(ln)
                        done[d["id"]] = d
        except FileNotFoundError:
            pass
    return done


def run(conn, rows: list[dict], out: str | None, apply: bool = False, workers: int = 3, day: dt.date | None = None, runner=None, sleep=time.sleep, log=db.log,
        from_records: dict | None = None) -> dict:
    day = day or dt.date.today()
    version = VERSION_PREFIX + day.isoformat()
    runner = runner or at.subprocess.run
    t0 = time.time()
    stats = {"version": version, "apply": apply, "rows": len(rows), "model_rows": 0, "skipped_done": 0, "errors": 0, "calls": 0, "attempts": 0, "input_tokens": 0, "output_tokens": 0,
             "applied": 0, "same_line": 0, "exists": 0, "stale": 0, "apply_error": 0, "stopped_early": None}
    cats = {s: at.load_catalog(s) for s in at.PRODUCTS}
    by_code = {s: {c["line"]: c for c in cats[s]} for s in cats}
    line_ids = {(s, code): v["id"] for s in cats for code, v in at.taxonomy_ids(conn, s).items()}
    done = read_done(out)
    fh = open(out, "a") if out and not from_records else None

    def emit(rec):
        if fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            fh.flush()
        if apply and rec.get("new_line"):
            st = apply_record(conn, rec, version, line_ids)
            conn.commit()
            stats["apply_error" if st == "error" else st] += 1

    try:
        if from_records is not None:                              # apply reviewed results, no model call
            for r in rows:
                rec = from_records.get(r["id"])
                if rec:
                    emit(rec)
            return stats
        todo = []
        for r in rows:
            if r["id"] in done and not done[r["id"]].get("error"):
                stats["skipped_done"] += 1
                if apply:
                    emit_done = done[r["id"]]
                    st = apply_record(conn, emit_done, version, line_ids)
                    conn.commit()
                    stats["apply_error" if st == "error" else st] += 1
                continue
            todo.append(r)
        prepared = [(r,) + prepare(conn, r) for r in todo]         # DB reads on this thread, model calls in the pool

        def work(item):
            r, unit, img = item
            try:
                ans, usage = at.tag_one(r["slug"], cats[r["slug"]], unit, img, runner, sleep)
                return r, unit, img, ans, usage, None
            except at.CallFailed as e:
                return r, unit, img, None, None, db.scrub(e)[:200]
            except at.StopRun as e:
                return r, unit, img, None, None, "STOP:" + db.scrub(e)[:200]

        with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = [ex.submit(work, it) for it in prepared]
            for fut in cf.as_completed(futs):
                r, unit, img, ans, usage, err = fut.result()
                if err and err.startswith("STOP:"):
                    stats["stopped_early"] = err[5:]
                    for f2 in futs:
                        f2.cancel()
                    break
                stats["model_rows"] += 1
                if usage:
                    for k in ("calls", "attempts", "input_tokens", "output_tokens"):
                        stats[k] += usage[k]
                rec = result_record(r, by_code[r["slug"]], ans, usage, unit, img is not None, err)
                if err:
                    stats["errors"] += 1
                emit(rec)
                if stats["model_rows"] % 10 == 0:
                    log(f"  retag_pain {stats['model_rows']}/{len(prepared)} rows, {round(time.time() - t0)}s")
    finally:
        if fh:
            fh.close()
    stats["duration_s"] = round(time.time() - t0, 1)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", choices=["mismatch"], help="which rows to re-tag")
    ap.add_argument("--limit", type=int, help="the N biggest spend_30d rows only")
    ap.add_argument("--ids", help="comma-separated tracker_rows ids (still must match the selection)")
    ap.add_argument("--out", help="JSON lines results file (resumable: rows already in it are skipped)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--apply", action="store_true", help="WRITE ad_tags (one transaction per row). Without it nothing is written.")
    ap.add_argument("--from", dest="from_file", help="with --apply: apply the results in this JSON lines file instead of calling the model")
    ap.add_argument("--revert", metavar="VERSION", help="restore the previous tags of a retag-pain-* version")
    a = ap.parse_args()
    if a.revert:
        with db.connect() as conn:
            print(json.dumps(revert(conn, a.revert)))
        return
    if not a.select:
        ap.error("--select mismatch (or --revert VERSION) is required")
    ids = [int(x) for x in a.ids.split(",")] if a.ids else None
    conn = db.connect() if a.apply else connect_ro()
    with conn:
        rows = select_mismatch(conn, ids, a.limit)
        db.log(f"retag_pain: {len(rows)} rows selected")
        recs = None
        if a.from_file:
            if not a.apply:
                ap.error("--from needs --apply")
            recs = read_done(a.from_file)
        st = run(conn, rows, a.out, a.apply, a.workers, from_records=recs)
        db.log(json.dumps(st, default=str))
        if st["stopped_early"]:
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                           # noqa: BLE001
        db.log("retag_pain FAILED:", db.scrub(e))
        sys.exit(1)
