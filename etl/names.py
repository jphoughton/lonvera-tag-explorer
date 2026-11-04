"""Keep meta_ads.family_name / meta_family_key equal to fam(ad_name) for every ad (a renamed ad's history follows its current name).
Usage: python -m etl.names"""
from __future__ import annotations

from . import db
from .resolver import fam


def resync(conn) -> dict:
    rows = conn.execute("SELECT ad_id, ad_name, meta_family_key, family_name FROM meta_ads WHERE ad_name IS NOT NULL").fetchall()
    upd = []
    for ad_id, name, key, fname in rows:
        f = fam(name)
        if f.lower() != key or f != fname:
            upd.append((f.lower(), f, ad_id))
    db.execmany(conn, "UPDATE meta_ads SET meta_family_key=%s, family_name=%s WHERE ad_id=%s", upd)
    changed_keys = conn.execute("SELECT 1").fetchone() and len(upd)
    return {"ads": len(rows), "updated": changed_keys}


def main():
    with db.JobRun("names") as r, db.connect() as conn:
        r.stats = resync(conn)
        conn.commit()
        db.log(f"  {r.stats}")


if __name__ == "__main__":
    main()
