"""Feed loaders: parsers on the sheet snapshot, idempotent trailing-window refresh, empty-tab error, freshness, Meta aggregates from ad_daily."""
import datetime as dt

import pytest

from app.projection.pacing import feedspec
from pacing_support import SNAP
from etl import projection_pacing as ETL


def scalar(conn, sql, *a):
    return next(iter(conn.execute(sql, a).fetchone().values()))


def test_parsers_read_the_snapshot_grids():
    orders = ETL.parse_orders(ETL.read_grid("Orders - History", SNAP))
    last = max(orders)
    assert last[0] == dt.date(2026, 11, 2) and last[1:] == (7777.05, 28159.16, 116.0, 35936.21, 611.0, 393.84, 3433.4)
    amz = {r[0]: r for r in ETL.parse_amazon(ETL.read_grid("Amazon - Daily", SNAP))}
    assert amz[dt.date(2026, 11, 3)][3] == 785.51                      # revenue of today's partial row is loaded (the engine ignores it)
    reach = {(r[0], r[1]): r for r in ETL.parse_meta_reach(ETL.read_grid("Meta Account - Reach", SNAP))}
    month = reach[("month", "2026-11")]
    assert month[4] == 481724.0 and month[5] == 167048.0 and month[8] == 8662.0     # BackUp account contributes zero
    assert all(g in ("day", "week", "month") for g, _ in reach)
    hist = ETL.parse_meta_campaign_history(ETL.read_grid("Meta Campaign - History", SNAP))
    assert any(r[3] == "Boneflex" and r[4] == "Scaling" for r in hist)


def test_hourly_curve_has_24_hours(conn):
    assert scalar(conn, "SELECT count(*) FROM proj_pacing_hourly") == 24
    assert round(scalar(conn, "SELECT sum(spend_30d) FROM proj_pacing_hourly")) == 453210


def test_backfill_loaded_every_feed(conn):
    runs = {r["feed"]: r for r in conn.execute("SELECT * FROM proj_feed_runs")}
    for s in feedspec.FEED_SPECS:
        assert runs[s.key]["rows_loaded"] > 0, s.key
    assert runs["orders"]["last_day"] == dt.date(2026, 11, 2)


def test_refresh_is_idempotent(clean_conn):
    before = scalar(clean_conn, "SELECT count(*) FROM proj_feed_orders")
    a = ETL.refresh(clean_conn, days=3, snapshot_dir=SNAP)
    b = ETL.refresh(clean_conn, days=3, snapshot_dir=SNAP)
    assert a["rows"] == b["rows"] > 0
    assert a["data_through"] == "2026-11-02"
    assert scalar(clean_conn, "SELECT count(*) FROM proj_feed_orders") == before


def test_refresh_replaces_only_the_trailing_window(clean_conn):
    clean_conn.execute("UPDATE proj_feed_orders SET nc_rev = 1 WHERE day IN (%s, %s)", (dt.date(2026, 11, 2), dt.date(2026, 10, 1)))
    clean_conn.commit()
    ETL.refresh(clean_conn, days=3, snapshot_dir=SNAP)
    assert float(scalar(clean_conn, "SELECT nc_rev FROM proj_feed_orders WHERE day = %s", dt.date(2026, 11, 2))) == 7777.05        # inside the window: restored
    assert float(scalar(clean_conn, "SELECT nc_rev FROM proj_feed_orders WHERE day = %s", dt.date(2026, 10, 1))) == 1.0            # older: untouched
    ETL.refresh(clean_conn, days=60, snapshot_dir=SNAP)
    assert float(scalar(clean_conn, "SELECT nc_rev FROM proj_feed_orders WHERE day = %s", dt.date(2026, 10, 1))) != 1.0
    clean_conn.rollback()


def test_empty_tab_raises_a_clear_error_and_writes_nothing(clean_conn, monkeypatch):
    real = ETL.read_grid
    monkeypatch.setattr(ETL, "read_grid", lambda tab, snap=None: [] if tab == "Amazon - Daily" else real(tab, snap))
    clean_conn.execute("UPDATE proj_feed_orders SET nc_rev = 5 WHERE day = %s", (dt.date(2026, 11, 2),))
    clean_conn.commit()
    with pytest.raises(ETL.FeedError) as e:
        ETL.refresh(clean_conn, days=3, snapshot_dir=SNAP)
    assert "Amazon - Daily" in str(e.value)
    clean_conn.rollback()
    assert float(scalar(clean_conn, "SELECT nc_rev FROM proj_feed_orders WHERE day = %s", dt.date(2026, 11, 2))) == 5.0            # orders feed was not touched
    clean_conn.execute("UPDATE proj_feed_orders SET nc_rev = 7777.05 WHERE day = %s", (dt.date(2026, 11, 2),))
    clean_conn.commit()


def test_unreadable_tab_raises_feed_error(monkeypatch):
    with pytest.raises(ETL.FeedError):
        ETL.read_grid("No Such Tab", SNAP)


def test_freshness_values_and_shape(conn):
    fresh = {f["feed"]: f for f in ETL.freshness(conn)}
    assert fresh["orders"]["data_through"] == dt.date(2026, 11, 2)
    assert fresh["amazon"]["data_through"] == dt.date(2026, 11, 3)
    assert fresh["platform"]["data_through"] == dt.date(2026, 11, 2)
    assert fresh["anyclick"]["data_through"] == dt.date(2026, 11, 2)
    assert fresh["meta_reach"]["data_through"] == dt.date(2026, 11, 3)
    assert "meta_ad_daily" in fresh
    for f in fresh.values():
        assert f["label"] and f["cadence"] in ("daily", "monthly-upload")
        assert f["stale_after_days"] == (45 if f["feed"] == "hourly_curve" else 2) and f["cadence"] == ("monthly-upload" if f["feed"] == "hourly_curve" else "daily")
    assert fresh["hourly_curve"]["data_through"] is not None


def _seed_ad_daily(conn):
    conn.execute("DELETE FROM ad_daily")
    conn.execute("DELETE FROM meta_campaigns")
    conn.execute("DELETE FROM meta_ads")
    conn.execute("INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES ('c1','Main','Beef Liver Testing','testing'),('c2','Main','All_WW_Scaling','scaling')")
    conn.execute("INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_name) VALUES "
                 "('a1','Main','x','x','c1','Liver adset'),('a2','Main','y','y','c2','Boneflex 1,2'),('a3','Main','z','z','c2','All adset')")
    day = dt.date(2026, 11, 2)
    for ad, camp, sp in (("a1", "c1", 100), ("a2", "c2", 200), ("a3", "c2", 50)):
        conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, source, impressions) "
                     "VALUES (%s, %s, 'k', 'K', %s, %s, 2, %s, 10, 'meta_api', 1000)", (ad, day, camp, sp, sp * 2))


def test_meta_aggregates_from_ad_daily_by_campaign_and_by_product(meta_sandbox):
    clean_conn = meta_sandbox
    _seed_ad_daily(clean_conn)
    ETL.refresh(clean_conn, days=3, snapshot_dir=SNAP)
    rows = {(r["campaign_id"]): r for r in clean_conn.execute("SELECT * FROM proj_feed_meta_day WHERE source='app' AND day = '2026-11-02'")}
    assert float(rows["c1"]["spend"]) == 100 and rows["c1"]["stage"] == "Testing" and rows["c1"]["product"] == "Beef Liver"
    assert float(rows["c2"]["spend"]) == 250 and rows["c2"]["stage"] == "Scaling" and rows["c2"]["product"] == "Unclassified"
    prod = {r["product"]: float(r["spend"]) for r in clean_conn.execute("SELECT * FROM proj_feed_meta_product WHERE source='app' AND day = '2026-11-02'")}
    assert prod == {"Beef Liver": 100.0, "Boneflex": 200.0, "Other": 50.0}                 # campaign name, else ad set name, else Other
    fresh = {f["feed"]: f for f in ETL.freshness(clean_conn)}
    assert fresh["meta_ad_daily"]["data_through"] == dt.date(2026, 11, 2)


def test_app_rows_win_over_sheet_rows_for_the_same_day(meta_sandbox):
    from app.projection.pacing import feeds
    clean_conn = meta_sandbox
    _seed_ad_daily(clean_conn)
    ETL.refresh(clean_conn, days=3, snapshot_dir=SNAP)
    fr = feeds.load_frame(clean_conn, dt.date(2026, 11, 1), dt.date(2026, 11, 2))
    assert fr.meta_source[dt.date(2026, 11, 2)] == "app" and fr.days[dt.date(2026, 11, 2)]["m_spend"] == 350
    assert fr.meta_source[dt.date(2026, 11, 1)] == "sheet"                                  # no ad_daily row: the sheet history is used


def test_dict_row_and_tuple_row_connections_both_work(pacing_db):
    import psycopg
    with psycopg.connect(pacing_db) as c:                                                  # tuple rows
        assert ETL.freshness(c)[0]["data_through"] == dt.date(2026, 11, 2)
