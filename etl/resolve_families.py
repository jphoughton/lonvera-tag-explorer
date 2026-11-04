"""Map Meta families -> tracker families with the ported Resolver. Inserts map rows for Meta families seen in ad_daily that have none,
and retries every family whose tracker_family_key is NULL (new ads, previously unmatched, tracker rows added since). Existing matches are left alone.
Usage: python -m etl.resolve_families"""
from __future__ import annotations

from . import db
from .resolver import Resolver, fam


def all_meta_families(conn) -> dict[str, str]:
    """meta_family_key -> display name (original case) across ad_daily, meta_ads and meta_ad_lifetime."""
    out: dict[str, str] = {}
    for k, name in conn.execute("SELECT DISTINCT ON (meta_family_key) meta_family_key, meta_ad_name FROM (SELECT meta_family_key, ad_name AS meta_ad_name FROM meta_ads "
                                "WHERE meta_family_key IS NOT NULL AND ad_name IS NOT NULL) x ORDER BY meta_family_key").fetchall():
        out[k] = fam(name) or k
    for k, name in conn.execute("SELECT DISTINCT ON (meta_family_key) meta_family_key, meta_family FROM ad_daily ORDER BY meta_family_key, day DESC").fetchall():
        out[k] = name                                                      # ad_daily display names come straight from fam(ad_name)
    for (k,) in conn.execute("SELECT DISTINCT meta_family_key FROM meta_ad_lifetime WHERE meta_family_key IS NOT NULL").fetchall():
        out.setdefault(k, k)
    return out


def resolve(conn, rebuild: bool = False) -> dict:
    """rebuild=True drops the whole map first (e.g. to discard the sheet's Windows mapping) and re-resolves every Meta family."""
    trows = [{"family": f, "concept": c, "adname": n or ""} for f, c, n in
             conn.execute("SELECT ad_family, concept_id, meta_ad_name FROM tracker_rows").fetchall()]
    tracker_keys = {f.strip().lower() for f, in conn.execute("SELECT DISTINCT ad_family FROM tracker_rows WHERE ad_family <> ''").fetchall()}
    created = {k: d.isoformat() for k, d in conn.execute(
        "SELECT meta_family_key, min(created_date) FROM meta_ads WHERE created_date IS NOT NULL GROUP BY 1").fetchall()}
    res = Resolver(trows, created)
    if rebuild:
        conn.execute("TRUNCATE meta_family_map")
    existing = {k: tk for k, tk in conn.execute("SELECT meta_family_key, tracker_family_key FROM meta_family_map").fetchall()}
    fams = all_meta_families(conn)
    todo = [(k, name, False) for k, name in fams.items() if k not in existing]
    todo += [(k, fams.get(k, k), True) for k, tk in existing.items() if tk is None]
    inserted = matched = 0
    by_rule: dict = {}
    for k, name, retry in todo:
        tf, rule = res.resolve_rule(name)
        tk = tf.strip().lower() if tf else None
        if tk and tk not in tracker_keys:
            tk, rule = None, None
        if not retry:
            conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (k, name, tk, rule))
            inserted += 1
        elif tk:
            conn.execute("UPDATE meta_family_map SET tracker_family_key=%s, rule=%s, resolved_at=now() WHERE meta_family_key=%s", (tk, rule, k))
        if tk:
            matched += 1
            by_rule[rule] = by_rule.get(rule, 0) + 1
    still = conn.execute("SELECT count(*) FROM meta_family_map WHERE tracker_family_key IS NULL").fetchone()[0]
    return {"new_meta_families": inserted, "newly_matched": matched, "by_rule": by_rule, "still_unmatched": still, "rebuild": rebuild}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="drop the whole map and re-resolve every Meta family with the Resolver")
    a = ap.parse_args()
    with db.JobRun("resolve_families") as run, db.connect() as conn:
        run.stats = resolve(conn, a.rebuild)
        conn.commit()
        db.log(f"  {run.stats}")


if __name__ == "__main__":
    main()
