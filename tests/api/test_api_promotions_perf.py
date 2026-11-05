"""Performance guard for Test to scale: a synthetic account the size of production (about 2.7K creatives, 135K ad x day rows, 54K family x day rows) is loaded through
the real ETL builders; every window must answer from the precomputed tables well inside the budget, and the refresh itself must stay reasonable. The 2 s budget is
the product target; the test allows 3 s so a loaded CI machine does not flake, and checks the first (uncached) call of each window."""
from __future__ import annotations

import datetime as dt
import time

import psycopg
import pytest

from etl import build_metrics

from . import seed_fixture as S
from .conftest import DB_NAME, PG_BASE, PASSWORD, make_app

D0, THROUGH = dt.date(2026, 5, 6), dt.date(2026, 11, 3)
URL = f"{PG_BASE}/{DB_NAME}_promoperf"
N = 2650
BUDGET = 3.0


@pytest.fixture(scope="module")
def big():
    try:
        S.recreate_db(f"{PG_BASE}/postgres", f"{DB_NAME}_promoperf")
        conn = psycopg.connect(URL)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    with conn:
        S.apply_migrations(conn)
        pid = conn.execute("SELECT id FROM products WHERE slug = 'vita-bull'").fetchone()[0]
        conn.execute("""INSERT INTO meta_campaigns (campaign_id, account, name, stage)
                        SELECT 'c' || g, 'Lonvera Main', CASE WHEN g <= 10 THEN 'T_Test_' ELSE 'S_Scaling_' END || g, 'scaling' FROM generate_series(1, 40) g""")
        conn.execute("""INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, status, ad_type, raw)
                        SELECT %s, 'f' || g || '#1', 'C' || g, 'F' || g, 'f' || g, g, '8 - Launched', 'VID', '{}' FROM generate_series(1, %s) g""", (pid, N))
        conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) SELECT 'f' || g, 'F' || g, 'f' || g, 'exact' FROM generate_series(1, %s) g", (N,))
        # one testing ad per family (10 days from its start), a scaling ad for 40% (15 days, 12 days after the start), a second scaling ad for 10%; a mass relaunch day
        conn.execute("""CREATE TEMP TABLE ads AS
            SELECT 'a' || g || 't' AS ad_id, g, 'c' || (1 + g %% 10) AS camp, 5 + (g * 7) %% 150 AS start, 25 AS n FROM generate_series(1, %(n)s) g
            UNION ALL SELECT 'a' || g || 's', g, 'c' || (11 + g %% 29), CASE WHEN g %% 20 = 0 THEN 150 ELSE 5 + (g * 7) %% 150 + 12 END, 40 FROM generate_series(1, %(n)s) g WHERE g %% 5 < 2
            UNION ALL SELECT 'a' || g || 'x', g, 'c' || (11 + (g + 7) %% 29), 5 + (g * 7) %% 150 + 14, 30 FROM generate_series(1, %(n)s) g WHERE g %% 10 = 0""", {"n": N})
        conn.execute("""INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_id, adset_name, created_date, status, family_name)
                        SELECT ad_id, 'Lonvera Main', 'F' || g, 'f' || g, camp, 's' || ad_id, 'AS ' || ad_id, DATE '2026-05-01', 'ACTIVE', 'F' || g FROM ads""")
        conn.execute("""INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, impressions, source)
                        SELECT ad_id, %s::date + (start + k), 'f' || g, 'F' || g, camp, 10 + (g %% 7) * 5, g %% 3, (g %% 3) * 70, 10, 500, 'meta_api'
                        FROM ads, LATERAL generate_series(0, n - 1) k WHERE start + k <= 179""", (D0,))
        conn.execute("INSERT INTO app_meta (key, value) VALUES ('daily_start', %s)", (D0.isoformat(),))
        build_metrics.rebuild(conn, commit=False)
        conn.execute("""INSERT INTO ad_daily_upstack (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, nc_purchases, nc_conv_value, link_clicks, impressions)
                        SELECT ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases * 2, conv_value * 2, purchases / 2, conv_value / 2, link_clicks, impressions FROM ad_daily""")
        for b in ("nc", "all"):
            build_metrics.rebuild_source(conn, b)
        for k, v in (("upstack_through", THROUGH.isoformat()), ("upstack_first_day", D0.isoformat()), ("upstack_built_at", "2026-11-04T00:00:00+00:00")):
            conn.execute("INSERT INTO app_meta (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (k, v))
        conn.commit()
        t = time.time()
        conn.execute("SELECT refresh_creative_stage_moves()")
        conn.commit()
        refresh_s = time.time() - t
        sizes = {r[0]: r[1] for r in conn.execute("SELECT 'daily', count(*) FROM ad_daily UNION ALL SELECT 'family', count(*) FROM family_daily UNION ALL SELECT 'moved', count(*) FROM creative_stage_moves WHERE moved")}
    return {"refresh_s": refresh_s, "sizes": sizes}


@pytest.fixture(scope="module")
def pcl(big):
    a = make_app(DATABASE_URL=URL, DB_POOL_MAX=4)
    c = a.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    yield c
    a.extensions["pool"].close()


def test_synthetic_account_is_production_sized(big):
    assert big["sizes"]["daily"] > 100_000 and big["sizes"]["family"] > 40_000 and big["sizes"]["moved"] > 800
    assert big["refresh_s"] < 20, big["refresh_s"]                                  # the nightly rebuild of all six tables


@pytest.mark.parametrize("qs", [
    "window=7d", "window=14d", "window=30d", "window=60d", "window=90d", "window=180d", "window=mtd", "window=launch", "from=2026-08-01&to=2026-09-15",
    "window=launch&hide_bulk=1", "window=launch&min_test_spend=50", "window=90d&product=vita-bull", "window=launch&source=upstack&basis=nc",
    "window=launch&source=upstack&basis=all", "window=180d&hide_bulk=1&min_test_spend=100&grain=day",
])
def test_every_window_answers_fast_uncached(pcl, qs):
    t = time.time()
    r = pcl.get("/api/promotions?" + qs)
    dt_ = time.time() - t
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert dt_ < BUDGET, f"{qs}: {dt_:.2f}s"
    if qs == "window=launch":
        assert r.get_json()["summary"]["moved"]["n"] > 800
