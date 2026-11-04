"""Create + seed a throwaway Postgres database with the synthetic API fixture.

    .venv/bin/python -m tests.api.seed_fixture [--db tagexp_apitest] [--url-base postgresql://localhost]

Drops and recreates the named database, applies migrations/*.sql, loads tests/api/fixture_data.py (tracker rows, tags, Meta ad-level
and family-level daily rows, derived tables built by the oracle, one creative asset). Used by the API tests (conftest) and usable by
anyone who wants a deterministic dataset to point the UI at:

    DATABASE_URL=postgresql://localhost/tagexp_apitest APP_PASSWORD=x SECRET_KEY=y DEV_INSECURE_COOKIES=1 .venv/bin/flask --app wsgi run
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import os

import psycopg

from . import fixture_data as F

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def recreate_db(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')


def apply_migrations(conn) -> None:
    for path in sorted(glob.glob(os.path.join(ROOT, "migrations", "*.sql")) + [os.path.join(os.path.dirname(__file__), "schema_shim.sql")]):
        with open(path) as f:
            conn.execute(f.read())
    conn.commit()


def seed(conn) -> F.Fx:
    """conn: plain psycopg connection to an empty, migrated database. Returns the fixture oracle object."""
    pids = {slug: pid for pid, slug in conn.execute("SELECT id, slug FROM products").fetchall()}
    fx = F.build_fixture(pids)

    # taxonomy
    line_ids = {}
    for i, ln in enumerate(F.TAXONOMY):
        lid = conn.execute(
            """INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger, story, cluster_color, sort_order)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (pids["vita-bull"], ln["code"], ln["cluster"], ln["icp"], ln["pain"], ln["trig"], ln["story"], ln["color"], i + 1)).fetchone()[0]
        line_ids[ln["code"]] = lid
        for j, (ing, att, sup) in enumerate(ln["ingredients"]):
            conn.execute("INSERT INTO line_ingredients (line_id, ingredient, attacks, supports, sort_order) VALUES (%s,%s,%s,%s,%s)",
                         (lid, ing, att, sup, j))

    # tracker rows + tags
    for r in fx.tracker:
        tag = r["tag"]
        t_cluster = t_icp = t_pain = None
        if tag and tag[0] == "tracker":
            t_cluster, t_icp, t_pain = (None if v == "(none)" else v for v in tag[1:])
        conn.execute(
            """INSERT INTO tracker_rows (id, product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, style, vendor,
                   launch_date, concept_name, description, hypothesis, meta_ad_name, primary_text, headlines, ad_description, transcript,
                   asset_url, tracker_cluster, tracker_icp, tracker_pain, tracker_emotion, tracker_ingredient, raw)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (r["id"], r["product_id"], r["row_key"], r["concept"], r["family"], r["family_key"], r["sheet_row"], r["status"], r["ad_type"],
             "Photo-Hero", "Madre", r["launch"], f"Concept {r['concept']}", "short description", "why test this", f"{r['concept']} Name",
             [f"Primary text for {r['concept']}", "Second primary text"], ["Headline 1", "Headline 2"], "Ad description",
             f"Transcript of {r['concept']}", f"https://drive.example/{r['concept']}", t_cluster, t_icp, t_pain, "Fear", "Zinc", json.dumps({})))
        if tag is None:
            continue
        if tag[0] == "linemap":
            ln = next(x for x in F.TAXONOMY if x["code"] == tag[1])
            ex = tag[4]
            conn.execute(
                """INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger,
                       confidence, method, seen, why, runner_up, copy_only_line, tagger)
                   VALUES (%s,'linemap','linemap-v2-2026-11-03',true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'test')""",
                (r["id"], line_ids[tag[1]], ln["cluster"], ln["icp"], ln["pain"], ln["trig"], tag[2], tag[3], ex.get("seen"), ex.get("why"),
                 ex.get("runner_up"), ex.get("copy_only")))
        else:
            conn.execute(
                """INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, cluster, icp, pain_point, tagger)
                   VALUES (%s,'tracker','tracker-live',true,%s,%s,%s,'tracker')""", (r["id"], tag[1], tag[2], tag[3]))
    for r in F.DELETED_TRACKER:               # removed from the tracker: present in the table, deleted_at set (see fixture_data.DELETED_TRACKER)
        tag = r["tag"]
        conn.execute(
            """INSERT INTO tracker_rows (id, product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, launch_date, raw, deleted_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'8 - Launched','IMG',%s,%s, now())""",
            (r["id"], pids[r["product"]], r["concept"] + "#1", r["concept"], r["family"], F.fkey(r["family"], r["concept"]), r["sheet_row"],
             F.D(2026, 8, 1), json.dumps({})))
        if tag:
            conn.execute("""INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, cluster, icp, pain_point, tagger)
                            VALUES (%s,'tracker','tracker-live',true,%s,%s,%s,'tracker')""", (r["id"], tag[1], tag[2], tag[3]))
    conn.execute("SELECT setval(pg_get_serial_sequence('tracker_rows','id'), (SELECT max(id) FROM tracker_rows))")

    # meta side
    for cid, acct, name in F.CAMPAIGNS:
        conn.execute("INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES (%s,%s,%s,%s)",
                     (cid, acct, name, F.stage_of_campaign(name)))
    for k, (tfam, rule) in F.MAPPING.items():
        disp = next((v[0] for v in F.ADS.values() if F.meta_family_key(v[0]) == k), k)
        conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES (%s,%s,%s,%s)",
                     (k, disp, tfam, rule))
    for ad_id, (disp, camp, adset, status, created) in F.ADS.items():
        days = sorted(r["day"] for r in fx.ad_daily if r["ad_id"] == ad_id)
        conn.execute(
            """INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_id, adset_name, created_date, status, first_day, last_day)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (ad_id, F.AD_ACCOUNT.get(ad_id, "Lonvera Main"), f"{disp} ({ad_id})", F.meta_family_key(disp), camp, "S" + ad_id, adset, created, status,
             days[0] if days else None, days[-1] if days else None))
    for ad_id, lt in fx.ad_lifetime.items():          # API lifetime per Meta ad (CONTRACT v1.5)
        conn.execute(
            """INSERT INTO meta_ad_lifetime (ad_id, meta_family_key, tracker_family_key, spend, purchases, conv_value, impressions, link_clicks, source, pulled_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'meta_api', now())""",
            (ad_id, lt["mfk"], lt["tfam"], lt["spend"], lt["purchases"], lt["revenue"], lt["imp"], lt["clk"]))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, impressions, source)
               VALUES (%(ad_id)s,%(day)s,%(meta_family_key)s,%(meta_family)s,%(campaign_id)s,%(spend)s,%(purchases)s,%(conv_value)s,%(link_clicks)s,%(impressions)s,%(source)s)""",
            fx.ad_daily)
        cur.executemany(
            "INSERT INTO family_daily (family_key, day, stage, spend, purchases, revenue, link_clicks, impressions, clicks_tr, spend_tr) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(f, d, st, *v) for (f, d, st), v in fx.family_daily.items()])
        cur.executemany(
            """INSERT INTO ad_metrics (tracker_row_id, product_id, family_key, is_first_row, launch_date, spend_launch, purchases_launch, revenue_launch,
                   spend_7d, purchases_7d, revenue_7d, spend_30d, purchases_30d, revenue_30d, live, impressions_launch, clicks_launch)
               VALUES (%(tracker_row_id)s,%(product_id)s,%(family_key)s,%(is_first_row)s,%(launch_date)s,%(spend_launch)s,%(purchases_launch)s,
                   %(revenue_launch)s,%(spend_7d)s,%(purchases_7d)s,%(revenue_7d)s,%(spend_30d)s,%(purchases_30d)s,%(revenue_30d)s,%(live)s,
                   %(impressions_launch)s,%(clicks_launch)s)""",
            list(fx.ad_metrics.values()))
        cur.executemany(
            """INSERT INTO unmatched_meta (meta_family_key, meta_family, product_id, spend_7d, purchases_7d, revenue_7d, spend_30d, purchases_30d,
                   revenue_30d, spend_all, purchases_all, revenue_all, live, spend_launch, purchases_launch, revenue_launch, impressions_launch, clicks_launch)
               VALUES (%(meta_family_key)s,%(meta_family)s,%(product_id)s,%(spend_7d)s,%(purchases_7d)s,%(revenue_7d)s,%(spend_30d)s,%(purchases_30d)s,
                   %(revenue_30d)s,%(spend_all)s,%(purchases_all)s,%(revenue_all)s,%(live)s,%(spend_launch)s,%(purchases_launch)s,%(revenue_launch)s,
                   %(impressions_launch)s,%(clicks_launch)s)""",
            list(fx.unmatched.values()))

    # 'active ads only' data (v1.9), built by the active-view oracle
    fa = fx.active_view
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO family_daily_active (family_key, day, stage, spend, purchases, revenue, link_clicks, impressions, clicks_tr, spend_tr) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(f, d, st, *v) for (f, d, st), v in fa.family_daily.items()])
        for r in fx.tracker:
            am = fa.ad_metrics.get(r["id"])
            n_act = fa.n_active.get(r["family_key"], 0)
            if am is None or not am["is_first_row"]:          # carried by the first row only; ads_active_count sits on every row of the creative
                cur.execute("UPDATE ad_metrics SET ads_active_count = %s WHERE tracker_row_id = %s", (n_act, r["id"]))
                continue
            cur.execute(
                """UPDATE ad_metrics SET spend_launch_active=%s, purchases_launch_active=%s, revenue_launch_active=%s, impressions_launch_active=%s,
                       clicks_launch_active=%s, ads_active_count=%s, live_active=%s WHERE tracker_row_id=%s""",
                (am["spend_launch"], am["purchases_launch"], am["revenue_launch"], am["impressions_launch"], am["clicks_launch"], n_act, am["live"], r["id"]))

    # content + assets (family vb-fam-a gets a thumb, two frames and Meta copy)
    conn.execute("""INSERT INTO family_content (family_key, meta_body, meta_title, meta_desc, transcript, transcript_seconds, story_url, is_video, ad_id)
                    VALUES ('vb-fam-a','Meta body text','Meta title','Meta description','Spoken transcript',31,'https://facebook.example/post/1',true,'a1')""")
    for kind, idx in (("thumb", 0), ("frame", 1), ("frame", 2)):
        conn.execute("INSERT INTO creative_assets (family_key, kind, idx, mime, width, height, bytes, source) VALUES ('vb-fam-a',%s,%s,'image/png',1,1,%s,'test')",
                     (kind, idx, PNG_1X1))
    conn.execute("INSERT INTO creative_assets (family_key, kind, idx, mime, bytes, source) VALUES ('vb-fam-b','thumb',0,'image/jpeg',%s,'test')", (b"\xff\xd8\xff\xd9",))

    for k, v in (("daily_start", F.D0.isoformat()), ("data_through", F.THROUGH.isoformat())):
        conn.execute("INSERT INTO app_meta (key, value) VALUES (%s,%s)", (k, v))
    conn.execute("INSERT INTO etl_runs (job, started_at, finished_at, status, data_through) VALUES ('meta_pull', now() - interval '2 hours', now() - interval '1 hour 59 minutes', 'ok', %s)", (F.THROUGH,))
    conn.execute("INSERT INTO etl_runs (job, started_at, finished_at, status, data_through) VALUES ('build_metrics', now() - interval '1 hour', now() - interval '59 minutes', 'ok', %s)", (F.THROUGH,))
    conn.commit()
    return fx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="tagexp_apitest")
    ap.add_argument("--url-base", default=os.environ.get("TEST_PG_BASE", "postgresql://localhost"))
    a = ap.parse_args()
    recreate_db(f"{a.url_base}/postgres", a.db)
    with psycopg.connect(f"{a.url_base}/{a.db}") as conn:
        apply_migrations(conn)
        seed(conn)
    print(f"seeded {a.url_base}/{a.db}")


if __name__ == "__main__":
    main()
