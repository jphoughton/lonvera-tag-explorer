"""etl.shopify_orders: SKU -> product, order/line rows, window choice, idempotent upsert, deletion of orders older than 120 days, loud failure. A fake warehouse
answers the two statements; nothing touches the network or ExpandFi."""
import datetime as dt
from decimal import Decimal

import psycopg
import pytest

from etl import db, shopify_orders as so

TODAY = dt.date(2026, 11, 10)


@pytest.fixture(autouse=True)
def clean(dburl):
    with psycopg.connect(dburl, autocommit=True) as c:
        c.execute("TRUNCATE shopify_orders CASCADE")
    yield


def wrow(xid, sid, processed, cust="c1", total="100.00", cur="100.00", tags="", source="web", cancelled=None, test=None, lines=None, upd=None, country="US", codes=None):
    lines = [{"sku": "LV-VBULL-2", "name": "Vital Bull - 2 Bottles", "p": "50.00", "q": 2, "d": "5.00"}] if lines is None else lines
    return (xid, sid, "#1" + str(sid)[-3:], cust, processed, "USD", Decimal("95.00"), Decimal("0.00"), Decimal(total), "paid", None, "5.00", "0.00", cur, source, None, tags,
            codes, country, "CA", cancelled, test, lines, upd or dt.datetime(2026, 11, 1))


class FakeWH:
    """Answers FIRSTS_SQL with the ids in `firsts` and ORDERS_SQL with the rows whose processed_at falls in [a, b)."""
    def __init__(self, rows, firsts=()):
        self.rows, self.firsts, self.queries = rows, set(firsts), []

    def query(self, stmt, params=None):
        self.queries.append(params)
        if "firsts" in stmt and "DISTINCT ON" in stmt:
            return [(i,) for i in self.firsts]
        return [r for r in self.rows if params["a"] <= r[4] < params["b"]]


def utc(day, hour=18):
    return dt.datetime(day.year, day.month, day.day, hour, 0)


# ------------------------------------------------------------------ pure
@pytest.mark.parametrize("sku,want", [("LV-VBULL-2", "VitaBull"), ("LV-BLC180-3", "BeefLiver"), ("DPCD1-GN180001", "BeefLiver"), ("DPCD1-GN187001-X", "Boneflex"),
                                      ("LV-VBELLE-1", "VitaBelle"), ("DPCD1-GN179001", "VitaFlow"), ("SHIP-PROT", "Other"), (None, "Other"), ("", "Other")])
def test_sku_to_product(sku, want):
    assert so.product_of(sku) == want


def test_order_row_fields_flags_and_no_personal_data():
    r = wrow(7, 5001, utc(TODAY), total="100.00", cur="40.00", tags="Recurring, trybe", source=so.REBILL_SOURCE, cancelled="2026-11-02T10:00:00-08:00", test="true", codes="dc, x")
    row, lines = so.order_row(r, {7})
    o = dict(zip(so.ORDER_COLS, row))
    assert o["order_id"] == 5001 and o["is_new_customer"] and o["is_rebill"] and o["is_trybe_gift"] and o["is_test"] and o["is_cancelled"]
    assert o["refunded_amount"] == Decimal("60.00") and o["current_total_price"] == Decimal("40.00") and o["discount_codes"] == "dc, x"
    assert o["ship_country"] == "US" and o["ship_region"] == "CA" and o["customer_id"] == "c1" and o["line_count"] == 1
    assert not set(so.ORDER_COLS) & {"email", "name", "phone", "address", "first_name", "last_name"}
    assert dict(zip(so.LINE_COLS, lines[0]))["line_net"] == Decimal("95.00") and lines[0][4] == "VitaBull" and lines[0][1] == 1


def test_refund_never_negative_and_unusable_rows_skipped():
    row, _ = so.order_row(wrow(1, 10, utc(TODAY), total="50.00", cur="60.00"), set())
    assert dict(zip(so.ORDER_COLS, row))["refunded_amount"] == Decimal("0.00")
    assert so.order_row(wrow(2, "abc", utc(TODAY)), set()) is None


def test_order_date_is_the_pacific_day_of_processed_at():
    row, _ = so.order_row(wrow(1, 10, dt.datetime(2026, 11, 10, 3, 30)), set())          # 03:30 UTC on the 10th = 19:30 PST on the 9th
    assert dict(zip(so.ORDER_COLS, row))["order_date"] == dt.date(2026, 11, 9)


def test_choose_days():
    d = lambda n: TODAY - dt.timedelta(days=n)
    mon = dt.date(2026, 11, 9)                                                           # a Monday
    assert so.choose_days(mon, 0, None, None) == 120                                      # empty table = first load
    assert so.choose_days(mon, 5, d(119 + 1), mon) == 14
    assert so.choose_days(dt.date(2026, 11, 8), 5, d(121), dt.date(2026, 11, 8)) == 120   # Sunday = full refresh
    assert so.choose_days(mon, 5, mon - dt.timedelta(days=100), mon - dt.timedelta(days=20)) == 120   # gap longer than the trailing window
    assert so.choose_days(mon, 5, mon - dt.timedelta(days=60), mon) == 120                # table does not reach back to the window start
    assert so.choose_days(mon, 5, mon - dt.timedelta(days=119), mon, full=True) == 120
    assert so.choose_days(mon, 5, mon - dt.timedelta(days=119), mon, days=3) == 3
    assert so.choose_days(mon, 5, mon - dt.timedelta(days=119), mon, days=500) == 120


def test_slices_cover_the_window_without_gaps():
    s = so.slices(dt.date(2026, 7, 14), dt.date(2026, 11, 11))
    assert s[0][0] == so.pt_midnight_utc(dt.date(2026, 7, 14)) and s[-1][1] == so.pt_midnight_utc(dt.date(2026, 11, 11))
    assert all(a[1] == b[0] for a, b in zip(s, s[1:])) and len(s) == 12


# ------------------------------------------------------------------ pull + write
def test_pull_dedupes_by_shopify_order_id_keeping_the_latest_update():
    wh = FakeWH([wrow(1, 900, utc(TODAY), total="10.00", upd=dt.datetime(2026, 11, 1)), wrow(2, 900, utc(TODAY), total="12.00", upd=dt.datetime(2026, 11, 5))])
    orders, lines = so.pull(wh, TODAY, 14)
    assert len(orders) == 1 and orders[0][11] == Decimal("12.00") and len(lines) == 1


def test_run_loads_and_second_run_is_idempotent(conn):
    rows = [wrow(1, 101, utc(TODAY - dt.timedelta(days=3))), wrow(2, 102, utc(TODAY - dt.timedelta(days=1)), cust="c2", tags="trybe", total="0.00", cur="0.00")]
    wh = FakeWH(rows, firsts={1})
    st = so.run(conn, wh, TODAY)
    conn.commit()
    assert st["days"] == 120 and st["upserted"] == 2 and st["lines"] == 2 and st["new_customer_orders"] == 1
    snap = lambda: conn.execute("SELECT order_id, is_new_customer, is_trybe_gift, total_price, line_count FROM shopify_orders ORDER BY 1").fetchall()
    first = snap()
    so.run(conn, wh, TODAY, days=14)
    conn.commit()
    assert snap() == first
    assert conn.execute("SELECT count(*) FROM shopify_order_lines").fetchone()[0] == 2
    assert conn.execute("SELECT product, quantity, line_net FROM shopify_order_lines WHERE order_id = 101").fetchone() == ("VitaBull", 2, Decimal("95.00"))


def test_restated_order_overwrites_and_its_lines_are_replaced(conn):
    day = utc(TODAY - dt.timedelta(days=2))
    so.run(conn, FakeWH([wrow(1, 201, day, total="100.00", cur="100.00")]), TODAY); conn.commit()
    two = [{"sku": "LV-BLC180-1", "name": "Beef Liver", "p": "30.00", "q": 1, "d": "0.00"}]
    st = so.run(conn, FakeWH([wrow(1, 201, day, total="100.00", cur="70.00", lines=two, cancelled="2026-11-09T00:00:00Z")]), TODAY, days=14); conn.commit()
    o = conn.execute("SELECT refunded_amount, is_cancelled, line_count FROM shopify_orders WHERE order_id = 201").fetchone()
    assert o == (Decimal("30.00"), True, 1) and st["days"] == 14
    assert conn.execute("SELECT sku FROM shopify_order_lines WHERE order_id = 201").fetchall() == [("LV-BLC180-1",)]


def test_orders_older_than_120_days_are_deleted_with_their_lines(conn):
    old_day = TODAY - dt.timedelta(days=130)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO shopify_orders (order_id, processed_at, order_date) VALUES (1, %s, %s)", (utc(old_day), old_day))
        cur.execute("INSERT INTO shopify_order_lines (order_id, line_no, product) VALUES (1, 1, 'Other')")
    conn.commit()
    so.run(conn, FakeWH([wrow(2, 301, utc(TODAY))]), TODAY, days=14); conn.commit()
    assert [r[0] for r in conn.execute("SELECT order_id FROM shopify_orders").fetchall()] == [301]
    assert conn.execute("SELECT count(*) FROM shopify_order_lines WHERE order_id = 1").fetchone()[0] == 0


def test_empty_pull_fails_loudly_and_writes_nothing(conn):
    so.run(conn, FakeWH([wrow(1, 401, utc(TODAY))]), TODAY); conn.commit()
    with pytest.raises(db.JobFailed):
        so.run(conn, FakeWH([]), TODAY, days=14)
    assert conn.execute("SELECT count(*) FROM shopify_orders").fetchone()[0] == 1


def test_dry_run_writes_nothing(conn):
    st = so.run(conn, FakeWH([wrow(1, 501, utc(TODAY))]), TODAY, dry_run=True)
    assert st["dry_run"] and conn.execute("SELECT count(*) FROM shopify_orders").fetchone()[0] == 0


def test_main_records_a_failed_etl_run_and_exits_1(dburl, monkeypatch, capsys):
    import types
    monkeypatch.setenv("DATABASE_URL", dburl)
    monkeypatch.setattr(so.ef, "get_warehouse", lambda ctx: (_ for _ in ()).throw(so.fc.FeedError("expandfi: login refused")))
    with psycopg.connect(dburl, autocommit=True) as c:
        c.execute("DELETE FROM etl_runs WHERE job = 'shopify_orders'")
    assert so.main([]) == 1
    with psycopg.connect(dburl) as c:
        st, err = c.execute("SELECT status, error FROM etl_runs WHERE job = 'shopify_orders' ORDER BY id DESC LIMIT 1").fetchone()
    assert st == "failed" and "login refused" in err
