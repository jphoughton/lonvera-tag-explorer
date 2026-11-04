"""Map Meta families -> tracker families with the ported Resolver. Inserts map rows for Meta families seen in ad_daily that have none,
and retries every family whose tracker_family_key is NULL (new ads, previously unmatched, tracker rows added since). Existing matches are left alone.
Usage: python -m etl.resolve_families"""
from __future__ import annotations

from . import db
from .resolver import Resolver


def resolve(conn) -> dict:
    trows = [{"family": f, "concept": c, "adname": n or ""} for f, c, n in
             conn.execute("SELECT ad_family, concept_id, meta_ad_name FROM tracker_rows").fetchall()]
    tracker_keys = {f.strip().lower() for f, in conn.execute("SELECT DISTINCT ad_family FROM tracker_rows WHERE ad_family <> ''").fetchall()}
    created = {k: d.isoformat() for k, d in conn.execute(
        "SELECT meta_family_key, min(created_date) FROM meta_ads WHERE created_date IS NOT NULL GROUP BY 1").fetchall()}
    res = Resolver(trows, created)
    new_rows = conn.execute("""SELECT d.meta_family_key, (array_agg(d.meta_family ORDER BY d.day DESC))[1] FROM ad_daily d
                               LEFT JOIN meta_family_map m ON m.meta_family_key = d.meta_family_key WHERE m.meta_family_key IS NULL GROUP BY 1""").fetchall()
    todo = [(k, name, None) for k, name in new_rows]
    todo += conn.execute("SELECT meta_family_key, meta_family, NULL FROM meta_family_map WHERE tracker_family_key IS NULL").fetchall()
    inserted = matched = 0
    new_keys = {k for k, _ in new_rows}
    for k, name, _ in todo:
        tf, rule = res.resolve_rule(name)
        tk = tf.strip().lower() if tf else None
        if tk and tk not in tracker_keys:
            tk, rule = None, None
        if k in new_keys:
            conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (k, name, tk, rule))
            inserted += 1
            matched += bool(tk)
        elif tk:
            conn.execute("UPDATE meta_family_map SET tracker_family_key=%s, rule=%s, resolved_at=now() WHERE meta_family_key=%s", (tk, rule, k))
            matched += 1
    still = conn.execute("SELECT count(*) FROM meta_family_map WHERE tracker_family_key IS NULL").fetchone()[0]
    return {"new_meta_families": inserted, "newly_matched": matched, "still_unmatched": still}


def main():
    with db.JobRun("resolve_families") as run, db.connect() as conn:
        run.stats = resolve(conn)
        conn.commit()
        db.log(f"  {run.stats}")


if __name__ == "__main__":
    main()
