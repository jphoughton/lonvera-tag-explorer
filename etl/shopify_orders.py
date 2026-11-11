"""Order-level Shopify orders of the last 120 days (rolling) into Postgres: tables shopify_orders and shopify_order_lines (migration 210).

    python -m etl.shopify_orders [--days N] [--full] [--dry-run]

Source: ExpandFi warehouse, merchant 1452, table shopify_orders (READ ONLY, the same credential path as the other ExpandFi feeds: etl.feeds.expandfi_feeds.get_warehouse).
Runs on the `feeds` service (scripts/deploy/run_feeds.sh, cron 0 21 * * * UTC) after the feeds. No personal data is read into the app: no email, name, address or phone;
the customer is the Shopify customer id, the ship-to is country and region only.

Window and refresh
  * Window = the last 120 Pacific days including today (by processed_at, the order time; the warehouse's created_at is a sync time and is never used).
  * First load (empty table), a gap longer than the trailing window, and every Sunday (Pacific) pull the WHOLE window; every other night re-pulls the trailing 14 days,
    so restated, refunded and cancelled orders overwrite. --days N forces a window of N days; --full forces 120.
  * One transaction: upsert the pulled orders, replace their lines, delete orders older than the window (lines cascade). A pull that returns no order raises
    (nothing is written); any failure is recorded in etl_runs job 'shopify_orders' as failed and the process exits 1, so the Railway deployment shows red.

Rules (same as the new-customer revenue feed, etl.feeds.expandfi_feeds): new customer = the customer's first Shopify order ever (key = Shopify customer id, else lower(email);
earliest processed_at then id); product from the SKU prefix; test orders are kept and flagged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from . import db
from .feeds import common as fc
from .feeds import expandfi_feeds as ef

JOB = "shopify_orders"
WINDOW_DAYS = 120
TRAILING_DAYS = 14
CHUNK_DAYS = 10                       # one warehouse query per 10 Pacific-day slice, so none comes near the 150 s query cap
PT = ZoneInfo("America/Los_Angeles")
REBILL_SOURCE = "subscription_contract_checkout_one"
UPSERT_BATCH = 2000

PRODUCT_SLUG = {"VitaBull": "VitaBull", "Beef Liver": "BeefLiver", "Boneflex": "Boneflex", "Vital Belle": "VitaBelle", "Vital Flow": "VitaFlow", "Other": "Other"}

ORDER_COLS = ("order_id", "order_number", "processed_at", "order_date", "customer_id", "is_new_customer", "currency", "subtotal_price", "total_discounts", "shipping_price",
              "total_tax", "total_price", "current_total_price", "refunded_amount", "financial_status", "fulfillment_status", "source_name", "app_id", "tags", "discount_codes",
              "ship_country", "ship_region", "cancelled_at", "is_cancelled", "is_test", "is_rebill", "is_trybe_gift", "line_count")
LINE_COLS = ("order_id", "line_no", "sku", "product_name", "product", "quantity", "price", "line_discount", "line_net")

# Ids (ExpandFi row ids) of every customer's FIRST order ever whose processed_at is on or after %(since)s. One definition of 'new customer' with etl.feeds.expandfi_feeds.
FIRSTS_SQL = """
    WITH keyed AS (
        SELECT id, processed_at, COALESCE(NULLIF(shopify_customer_id, ''), NULLIF(lower(email), '')) AS ck
        FROM shopify_orders
        WHERE processed_at IS NOT NULL
    ),
    firsts AS (
        SELECT DISTINCT ON (ck) id, processed_at
        FROM keyed
        WHERE ck IS NOT NULL
        ORDER BY ck, processed_at, id
    )
    SELECT id FROM firsts WHERE processed_at >= %(since)s
"""

# One slice of orders: processed_at in [%(a)s, %(b)s) (UTC, the column's own zone). Only the fields we keep leave the warehouse.
ORDERS_SQL = """
    SELECT o.id, o.shopify_order_id, o.order_number, NULLIF(o.shopify_customer_id, ''), o.processed_at,
           o.currency, o.subtotal_price, o.total_tax, o.total_price, o.financial_status, o.fulfillment_status,
           o.raw_data->>'total_discounts',
           o.raw_data->'total_shipping_price_set'->'shop_money'->>'amount',
           o.raw_data->>'current_total_price',
           o.raw_data->>'source_name', o.raw_data->>'app_id', o.raw_data->>'tags',
           (SELECT string_agg(d->>'code', ', ') FROM jsonb_array_elements(COALESCE(o.raw_data->'discount_codes', '[]'::jsonb)) d),
           o.raw_data->'shipping_address'->>'country_code', o.raw_data->'shipping_address'->>'province_code',
           o.raw_data->>'cancelled_at', o.raw_data->>'test',
           (SELECT jsonb_agg(jsonb_build_object('sku', li->>'sku', 'name', li->>'name', 'p', li->>'price', 'q', li->>'quantity', 'd', li->>'total_discount') ORDER BY ord)
              FROM jsonb_array_elements(COALESCE(o.raw_data->'line_items', '[]'::jsonb)) WITH ORDINALITY AS t(li, ord)) AS lines,
           o.shopify_updated_at
    FROM shopify_orders o
    WHERE o.processed_at >= %(a)s AND o.processed_at < %(b)s
    ORDER BY o.processed_at, o.id
"""


# ---------------------------------------------------------------------------------------------------------------- pure helpers

def window_start(today: dt.date, days: int = WINDOW_DAYS) -> dt.date:
    """First Pacific day of a window of `days` days that ends today (inclusive)."""
    return today - dt.timedelta(days=days - 1)


def pt_midnight_utc(day: dt.date) -> dt.datetime:
    """Pacific midnight of `day` as a naive UTC datetime (processed_at in the warehouse is a UTC timestamp without a zone)."""
    return dt.datetime.combine(day, dt.time(), tzinfo=PT).astimezone(dt.timezone.utc).replace(tzinfo=None)


def slices(since: dt.date, until_excl: dt.date, step: int = CHUNK_DAYS) -> list[tuple[dt.datetime, dt.datetime]]:
    out, d = [], since
    while d < until_excl:
        e = min(d + dt.timedelta(days=step), until_excl)
        out.append((pt_midnight_utc(d), pt_midnight_utc(e)))
        d = e
    return out


def choose_days(today: dt.date, count: int, min_date: dt.date | None, max_date: dt.date | None, days: int | None = None, full: bool = False) -> int:
    """How many Pacific days to pull: the caller's N, the whole window (--full, an empty table, a table that stops short of the trailing window or does not
    reach back to the window start, and every Sunday), else the trailing 14."""
    if days:
        return min(int(days), WINDOW_DAYS)
    if full or count == 0 or max_date is None or min_date is None:
        return WINDOW_DAYS
    if max_date < today - dt.timedelta(days=TRAILING_DAYS - 1):
        return WINDOW_DAYS
    if min_date > window_start(today) + dt.timedelta(days=2):
        return WINDOW_DAYS
    if today.weekday() == 6:
        return WINDOW_DAYS
    return TRAILING_DAYS


def dec(x: Any) -> Decimal | None:
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def money(x: Any) -> Decimal:
    return dec(x) or Decimal("0.00")


def tstamp(x: Any) -> dt.datetime | None:
    """A Shopify ISO timestamp (with offset) -> aware datetime; None when blank."""
    if not x:
        return None
    try:
        t = dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def product_of(sku: str | None) -> str:
    return PRODUCT_SLUG[ef.product_of(sku)]


def order_row(r: tuple, first_ids: set) -> tuple[tuple, list[tuple]] | None:
    """One warehouse row -> (orders row in ORDER_COLS order, [lines rows in LINE_COLS order]); None when the row has no usable Shopify order id or time."""
    (xid, sid, number, cust, processed, currency, subtotal, tax, total, fin, ful, disc, ship, cur_total, source, app_id, tags, codes, country, region,
     cancelled_at, test, lines, _upd) = r
    try:
        oid = int(str(sid).strip())
    except (TypeError, ValueError):
        return None
    if processed is None:
        return None
    pts = processed.replace(tzinfo=dt.timezone.utc) if processed.tzinfo is None else processed
    order_date = pts.astimezone(PT).date()
    total_d, cur_d = money(total), dec(cur_total)
    refunded = max(total_d - cur_d, Decimal("0.00")) if cur_d is not None else Decimal("0.00")
    line_rows: list[tuple] = []
    for i, li in enumerate(lines or [], 1):
        price, q, d = money(li.get("p")), int(float(li.get("q") or 0)), money(li.get("d"))
        net = max(price * q - d, Decimal("0.00"))
        line_rows.append((oid, i, li.get("sku") or None, li.get("name") or None, product_of(li.get("sku")), q, price, d, net))
    tag_s = (tags or "").strip()
    canc = tstamp(cancelled_at)
    row = (oid, number, pts, order_date, cust, xid in first_ids, currency, dec(subtotal), dec(disc), dec(ship), dec(tax), total_d, cur_d, refunded, fin, ful,
           source, app_id or None, tag_s or None, codes or None, country or None, region or None, canc, canc is not None, str(test).lower() == "true",
           source == REBILL_SOURCE, "trybe" in {t.strip().lower() for t in tag_s.split(",")}, len(line_rows))
    return row, line_rows


# ---------------------------------------------------------------------------------------------------------------- pull

def pull(wh, today: dt.date, days: int) -> tuple[list[tuple], list[tuple]]:
    """Orders and lines for the last `days` Pacific days up to now. Duplicates of one Shopify order id keep the most recently updated warehouse row."""
    since = today - dt.timedelta(days=days - 1)
    first_ids = {r[0] for r in wh.query(FIRSTS_SQL, {"since": pt_midnight_utc(since)})}
    by_id: dict[int, tuple[Any, tuple, list[tuple]]] = {}
    for a, b in slices(since, today + dt.timedelta(days=1)):
        for r in wh.query(ORDERS_SQL, {"a": a, "b": b}):
            got = order_row(r, first_ids)
            if got is None:
                continue
            key, upd = got[0][0], r[-1] or dt.datetime.min
            if key not in by_id or upd >= by_id[key][0]:
                by_id[key] = (upd, got[0], got[1])
    orders = [v[1] for v in by_id.values()]
    lines = [ln for v in by_id.values() for ln in v[2]]
    return orders, lines


# ---------------------------------------------------------------------------------------------------------------- write

def _batches(rows: list, n: int = UPSERT_BATCH) -> Iterable[list]:
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def write(conn, orders: list[tuple], lines: list[tuple], today: dt.date) -> dict:
    """Upsert, replace the lines of the pulled orders, drop what left the window. The caller commits (one transaction)."""
    cols = ", ".join(ORDER_COLS)
    ph = ", ".join(["%s"] * len(ORDER_COLS))
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in ORDER_COLS[1:])
    lcols, lph = ", ".join(LINE_COLS), ", ".join(["%s"] * len(LINE_COLS))
    with conn.cursor() as cur:
        for b in _batches(orders):
            cur.executemany(f"INSERT INTO shopify_orders ({cols}) VALUES ({ph}) ON CONFLICT (order_id) DO UPDATE SET {upd}, loaded_at = now()", b)
        ids = [o[0] for o in orders]
        for b in _batches(ids):
            cur.execute("DELETE FROM shopify_order_lines WHERE order_id = ANY(%s)", (b,))
        for b in _batches(lines):
            cur.executemany(f"INSERT INTO shopify_order_lines ({lcols}) VALUES ({lph})", b)
        cur.execute("DELETE FROM shopify_orders WHERE order_date < %s", (window_start(today),))
        deleted = cur.rowcount
    return {"upserted": len(orders), "lines": len(lines), "deleted_old": deleted}


def state(conn) -> tuple[int, dt.date | None, dt.date | None]:
    n, lo, hi = conn.execute("SELECT count(*), min(order_date), max(order_date) FROM shopify_orders").fetchone()
    return n, lo, hi


def run(conn, wh, today: dt.date, days: int | None = None, full: bool = False, dry_run: bool = False) -> dict:
    n, lo, hi = state(conn)
    d = choose_days(today, n, lo, hi, days, full)
    orders, lines = pull(wh, today, d)
    if not orders:
        raise db.JobFailed(f"shopify_orders: the warehouse returned no order for the last {d} days; nothing written")
    stats = {"days": d, "orders_pulled": len(orders), "lines_pulled": len(lines), "first_date": min(o[3] for o in orders).isoformat(),
             "last_date": max(o[3] for o in orders).isoformat(), "new_customer_orders": sum(1 for o in orders if o[5])}
    if dry_run:
        return {**stats, "dry_run": True}
    stats.update(write(conn, orders, lines, today))
    stats["rows_in_table"] = state(conn)[0]
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="pull and count, write nothing and record no etl_runs row")
    a = ap.parse_args(argv)
    ctx = fc.make_ctx()
    try:
        if a.dry_run:
            with db.connect() as conn:
                print(db.scrub(run(conn, ef.get_warehouse(ctx), ctx.today, a.days, a.full, True)))
            return 0
        with db.JobRun(JOB) as jr, db.connect() as conn:
            st = run(conn, ef.get_warehouse(ctx), ctx.today, a.days, a.full)
            conn.commit()
            jr.stats, jr.rows_written = st, st["upserted"]
            jr.data_through = dt.date.fromisoformat(st["last_date"])
        return 0
    except Exception as e:                                                  # noqa: BLE001  (no stack trace: it could carry a connection string; JobRun already logged and recorded it)
        print(f"[{JOB}] FAILED: {fc.scrub(f'{type(e).__name__}: {e}')}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
