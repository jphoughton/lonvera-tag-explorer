"""ExpandFi warehouse feeds (merchant 1452, read-only): Amazon sales and Ads by day, new-customer revenue by product (Shopify first orders), and Amazon
units by product. Server-side ports of the desktop jobs: `amazon_daily.py` (lonvera-sync-main: tabs `Amazon - Daily` and `Amazon - Product Daily`) and
`product_daily_sync.py` + `nc_core.py` (lonvera_nc_product: tab `NC Rev - By Product`). Same SQL, same rules, same columns; only the window is explicit
(since..until) instead of "the last 1,100 days".

Credential: one per run. `get_warehouse(ctx)` refreshes the stored OAuth login (etl.feeds.expandfi_auth, one refresh at a time under a Postgres advisory
lock because the refresh token rotates), mints the 30-minute read-only warehouse credential through the MCP tool `readonly_db_connection` and keeps it in
memory in `ctx.cache` so every feed of the run shares it. The credential is never printed, logged or stored; errors pass through scrub() and the
connection string is blanked out of any message. A failed login or query raises (AuthError / FeedError / FeedEmpty): nothing is written, the feed goes
stale and the attempt is logged, never a row of zeros.

Rules replicated from the desktop jobs:
  amazon        sales.purchase_date basis, sales_channel in (Amazon.com, Amazon.com B2B) joined to customers (inner join, as the desktop query does);
                new customer = purchase_date equals customers.cohort_day; Ads = amazon_ads_campaign_data de-duplicated v3 > v2 > v1 per campaign-day
                (raw is about 1.3x high); a missing revenue or Ads result aborts the pull (the desktop job aborts without touching the sheet).
  units_amazon  sales.quantity_shipped by product (SKU prefix first, product-name fallback, 'Liver' checked last because Vital Belle's title says
                "Bovine Liver"); 'Other' is not stored (the inventory module ignores it).
  nc_product    Shopify first order per customer (shopify_customer_id, else lower(email); earliest processed_at then id), dated on its Pacific processed_at
                day; test orders and orders without priced lines are skipped after the first order is chosen; revenue = total_price split across the order's
                product lines by net line price (price x quantity - discount); NC orders = orders whose largest product line is that product (chosen before
                Vital Belle, Vital Flow and unmatched SKUs are rolled into Other).
"""
from __future__ import annotations

import collections
import datetime as dt
from typing import Any, Callable

from psycopg import sql

from . import expandfi_auth as auth
from .common import AuthError, Ctx, FeedEmpty, FeedError, Span, log, num, round2, scrub
from .feed import Feed, Metric

CACHE_KEY = "expandfi.warehouse"
SALES_CHANNELS = ["Amazon.com", "Amazon.com B2B"]
NC_PRODUCTS = ("VitaBull", "Beef Liver", "Boneflex", "Other")            # tab 'NC Rev - By Product': Vital Belle / Vital Flow / unmatched roll into Other
QUERY_TIMEOUT_MS = 150_000
LOCK_WAIT_S = 120
# Product label (sheet 'Amazon - Product Daily') -> the slug the inventory module stores. Kept in step with
# app.projection.inventory_demand.constants.HEADER_PRODUCT by a test; 'Other' is deliberately absent.
AMAZON_SLUG = {"Vital Bull": "bull", "Beef Liver": "liver", "Boneflex": "boneflex", "Vital Belle": "belle", "Vital Flow": "flow"}


# ---------------------------------------------------------------------------------------------------------------- the warehouse connection

class _AuthRejected(Exception):
    """The warehouse refused the credential (expired or revoked): internal signal to mint a new one once."""


def _pg_connect(dsn: str, timeout_ms: int):
    import psycopg
    conn = psycopg.connect(dsn, connect_timeout=20, autocommit=True)
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SELECT set_config('statement_timeout', %s, false)", (str(int(timeout_ms)),))
    return conn


def _clean(e: BaseException, dsn: str) -> str:
    """An error message with the credential removed: the whole connection string, its user and password, then the generic scrub patterns."""
    s = f"{type(e).__name__}: {e}"
    try:
        from urllib.parse import urlparse
        u = urlparse(dsn)
        for part in (dsn, u.password, u.username):
            if part and len(part) > 3:
                s = s.replace(part, "***")
    except ValueError:
        pass
    return scrub(s, 220)


class Warehouse:
    """Read-only SQL against the ExpandFi warehouse with a credential held in memory. One connection per query (as the desktop `exq.py` does); when the
    warehouse rejects the credential once (30-minute lifetime) a new one is minted and the query retried; a second rejection raises AuthError."""

    def __init__(self, dsn: str, remint: Callable[[], str] | None = None, connect: Callable[[str, int], Any] = _pg_connect,
                 sleep: Callable[[float], None] = lambda s: None):
        self._dsn, self._remint, self._connect, self._sleep = dsn, remint, connect, sleep
        self.queries = 0

    def _run(self, stmt: str, params: Any, timeout_ms: int) -> list[tuple]:
        import psycopg
        try:
            conn = self._connect(self._dsn, timeout_ms)
        except psycopg.OperationalError as e:
            msg = str(e).lower()
            if "authentication failed" in msg or "password" in msg or "expired" in msg or "role" in msg:
                raise _AuthRejected from None
            raise FeedError(f"expandfi warehouse: cannot connect ({_clean(e, self._dsn)})") from None
        try:
            return [tuple(r) for r in conn.execute(stmt, params).fetchall()]
        except psycopg.errors.QueryCanceled:
            raise FeedError(f"expandfi warehouse: query exceeded {timeout_ms // 1000}s") from None
        except psycopg.Error as e:
            raise FeedError(f"expandfi warehouse: {_clean(e, self._dsn)}") from None
        finally:
            try:
                conn.close()
            except Exception:                                                       # noqa: BLE001
                pass

    def query(self, stmt: str, params: Any = None, timeout_ms: int = QUERY_TIMEOUT_MS) -> list[tuple]:
        self.queries += 1
        try:
            return self._run(stmt, params, timeout_ms)
        except _AuthRejected:
            if not self._remint:
                raise AuthError("expandfi warehouse: the credential was rejected") from None
        log("expandfi: warehouse credential rejected, minting a new one")
        self._dsn = self._remint()
        try:
            return self._run(stmt, params, timeout_ms)
        except _AuthRejected:
            raise AuthError("expandfi warehouse: the credential was rejected twice") from None


def _refresh_lock(app, ctx: Ctx):
    """Context manager: a Postgres session advisory lock so two processes never refresh the (rotating) ExpandFi token at once. Waits up to LOCK_WAIT_S."""
    class _L:
        def __enter__(self):
            waited = 0
            while not app.execute("SELECT pg_try_advisory_lock(hashtext('feeds.expandfi.refresh'))").fetchone()[0]:
                if waited >= LOCK_WAIT_S:
                    raise FeedError("expandfi: another process is refreshing the ExpandFi login; try again in a few minutes")
                ctx.sleep(5)
                waited += 5
            return self

        def __exit__(self, *exc):
            try:
                app.execute("SELECT pg_advisory_unlock(hashtext('feeds.expandfi.refresh'))")
            except Exception:                                                       # noqa: BLE001
                pass
            return False
    return _L()


def _mint(ctx: Ctx) -> tuple[str, Any]:
    """Stored login -> access token (one refresh, under the lock) -> MCP session -> a read-only warehouse connection string. -> (dsn, mcp client)."""
    import psycopg
    key, url = ctx.require("FEEDS_ENC_KEY"), ctx.require("DATABASE_URL")
    with psycopg.connect(url) as app:
        with _refresh_lock(app, ctx):
            tokens = auth.access_token(app, ctx, key)
    mcp = auth.McpClient(ctx, tokens.access_token)
    mcp.initialize()
    return auth.mint_dsn(mcp, auth.MERCHANT_ID), mcp


def get_warehouse(ctx: Ctx) -> Warehouse:
    """The run's warehouse: opened once (one token refresh, one minted credential) and shared by every ExpandFi feed through ctx.cache.
    A failed login is remembered for the run, so the feeds after the first do not attempt another refresh of a token that was just rejected."""
    w = ctx.cache.get(CACHE_KEY)
    if w is None:
        failed = ctx.cache.get(CACHE_KEY + ".failed")
        if failed is not None:
            raise type(failed)(str(failed)) from None
        try:
            dsn, mcp = _mint(ctx)
        except FeedError as e:
            ctx.cache[CACHE_KEY + ".failed"] = e
            raise

        def remint() -> str:
            try:
                return auth.mint_dsn(mcp, auth.MERCHANT_ID)                        # the access token is good for about an hour
            except AuthError:
                new_dsn, _ = _mint(ctx)
                return new_dsn
        w = ctx.cache[CACHE_KEY] = Warehouse(dsn, remint=remint, sleep=ctx.sleep)
    return w


# ---------------------------------------------------------------------------------------------------------------- Amazon: sales and Ads by day

REVENUE_SQL = """
    SELECT s.purchase_date::date AS day,
           COUNT(DISTINCT s.amazon_order_id) AS orders,
           SUM(s.quantity_shipped) AS units,
           ROUND(SUM(s.total_amount_usd)::numeric, 2) AS revenue_usd,
           COUNT(DISTINCT s.amazon_order_id) FILTER (WHERE s.purchase_date::text = c.cohort_day) AS nc_orders,
           ROUND(SUM(s.total_amount_usd) FILTER (WHERE s.purchase_date::text = c.cohort_day)::numeric, 2) AS nc_revenue
    FROM sales s
    JOIN customers c ON c.id = s.customer_id
    WHERE s.purchase_date BETWEEN %(since)s AND %(until)s
      AND s.sales_channel = ANY(%(channels)s)
    GROUP BY 1
    ORDER BY 1
"""

# v3 > v2 > v1 per campaign-day (the dedupe-ads-spend doctrine). The account has no amazon_ads_periods ledger, so the v1-fence clause is omitted, as in the desktop job.
ADS_SQL = """
    WITH ad_data_clean AS (
        SELECT campaign_id, date, api_report_type, api_version, cost_usd, sales_14d, clicks, impressions
        FROM amazon_ads_campaign_data
        WHERE date IS NOT NULL
          AND campaign_id IS NOT NULL
          AND api_report_type IN ('spCampaigns','sbCampaigns','sdCampaigns','sbvCampaigns')
          AND NOT (api_version = 'v1' AND api_report_type IN ('sbCampaigns','sdCampaigns'))
          AND date BETWEEN %(since)s AND %(until)s
    ),
    best_generation_per_campaign_date AS (
        SELECT campaign_id, date,
               MIN(CASE api_version WHEN 'v3' THEN 1 WHEN 'v2' THEN 2 WHEN 'v1' THEN 3 ELSE 9 END) AS best_rank
        FROM ad_data_clean
        GROUP BY campaign_id, date
    ),
    campaign_totals AS (
        SELECT cd.campaign_id, cd.date,
               SUM(cd.cost_usd) AS campaign_date_spend,
               SUM(cd.sales_14d) AS campaign_date_sales_14d,
               SUM(cd.clicks) AS campaign_date_clicks,
               SUM(cd.impressions) AS campaign_date_impressions
        FROM ad_data_clean cd
        JOIN best_generation_per_campaign_date b
          ON b.campaign_id = cd.campaign_id AND b.date = cd.date
         AND CASE cd.api_version WHEN 'v3' THEN 1 WHEN 'v2' THEN 2 WHEN 'v1' THEN 3 ELSE 9 END = b.best_rank
        GROUP BY cd.campaign_id, cd.date
    )
    SELECT date AS day,
           ROUND(SUM(campaign_date_spend)::numeric, 2) AS deduped_spend,
           ROUND(SUM(campaign_date_sales_14d)::numeric, 2) AS deduped_attr_sales_14d,
           SUM(campaign_date_clicks) AS deduped_clicks,
           SUM(campaign_date_impressions) AS deduped_impressions
    FROM campaign_totals
    GROUP BY 1
    ORDER BY 1
"""

AMAZON_COLS = ("day", "orders", "units", "revenue", "nc_orders", "nc_rev", "ad_spend", "attributed_sales", "clicks", "impressions")


def merge_amazon(rev_rows: list[tuple], ads_rows: list[tuple]) -> list[tuple]:
    """(day, orders, units, revenue, nc_orders, nc_rev) + (day, spend, attributed sales, clicks, impressions) -> one row per day, AMAZON_COLS order."""
    by_day: dict[dt.date, list[float]] = {}
    for day, orders, units, revenue, nc_orders, nc_rev in rev_rows:
        by_day[day] = [num(orders), num(units), round2(revenue), num(nc_orders), round2(nc_rev), 0.0, 0.0, 0.0, 0.0]
    for day, spend, attr, clicks, impressions in ads_rows:
        rec = by_day.setdefault(day, [0.0] * 9)
        rec[5], rec[6], rec[7], rec[8] = round2(spend), round2(attr), num(clicks), num(impressions)
    return [(d, *by_day[d]) for d in sorted(by_day)]


def pull_amazon(ctx: Ctx, span: Span) -> list[tuple]:
    w = get_warehouse(ctx)
    params = {"since": span.since, "until": span.until, "channels": SALES_CHANNELS}
    rev = w.query(REVENUE_SQL, params)
    if not rev:
        raise FeedEmpty("amazon: the revenue query returned no rows for the window; nothing written")
    ads = w.query(ADS_SQL, params)
    if not ads:
        raise FeedEmpty("amazon: the Ads query returned no rows for the window; nothing written (spend would read as zero)")
    rows = merge_amazon(rev, ads)
    last_rev = max(r[0] for r in rev)
    if last_rev > max(r[0] for r in ads):
        ctx.warn(f"amazon: Ads for {last_rev.isoformat()} not loaded yet (spend reads 0 on that day until the next run)")
    return rows


# ---------------------------------------------------------------------------------------------------------------- Amazon units by product

# SKU prefix first; product name only as a fallback; 'Liver' last because Vital Belle's listing title contains "Bovine Liver, Heart & Kidney".
PRODUCT_CASE_SQL = """
    CASE
        WHEN s.sku ILIKE 'LV-VBULL%%' THEN 'Vital Bull'
        WHEN s.sku ILIKE 'LV-BLC%%' THEN 'Beef Liver'
        WHEN s.sku ILIKE 'LV-BFLEX%%' THEN 'Boneflex'
        WHEN s.sku ILIKE 'LV-VBELLE%%' THEN 'Vital Belle'
        WHEN s.sku ILIKE '%%GN179001%%' THEN 'Vital Flow'
        WHEN s.product_name ILIKE '%%bull%%' THEN 'Vital Bull'
        WHEN s.product_name ILIKE '%%boneflex%%' THEN 'Boneflex'
        WHEN s.product_name ILIKE '%%flow%%' THEN 'Vital Flow'
        WHEN s.product_name ILIKE '%%belle%%' THEN 'Vital Belle'
        WHEN s.product_name ILIKE '%%liver%%' THEN 'Beef Liver'
        ELSE 'Other'
    END
"""

PRODUCT_SQL = f"""
    SELECT s.purchase_date::date AS day,
           {PRODUCT_CASE_SQL} AS product,
           COUNT(DISTINCT s.amazon_order_id) AS orders,
           SUM(s.quantity_shipped) AS units,
           ROUND(SUM(s.total_amount_usd)::numeric, 2) AS revenue_usd
    FROM sales s
    WHERE s.purchase_date BETWEEN %(since)s AND %(until)s
      AND s.sales_channel = ANY(%(channels)s)
    GROUP BY 1, 2
    ORDER BY 1, 2
"""

UNITS_COLS = ("location", "product", "day", "units", "source", "is_estimate")
UNITS_SOURCE = "expandfi-product-daily"                  # what the sheet-fed rows carry (etl.projection_inventory_demand.parse_product_daily)


def units_rows(product_rows: list[tuple]) -> list[tuple]:
    out = []
    for day, product, _orders, units, _rev in product_rows:
        slug = AMAZON_SLUG.get(str(product).strip())
        if slug is None:
            continue                                                                 # 'Other': not stored
        n = num(units)
        if n < 0:
            raise FeedError(f"amazon units: negative units on {day.isoformat()} ({product})")
        out.append(("amazon", slug, day, n, UNITS_SOURCE, False))
    return out


def pull_units_amazon(ctx: Ctx, span: Span) -> list[tuple]:
    rows = get_warehouse(ctx).query(PRODUCT_SQL, {"since": span.since, "until": span.until, "channels": SALES_CHANNELS})
    if not rows:
        raise FeedEmpty("amazon units: the product query returned no rows for the window; nothing written")
    out = units_rows(rows)
    if not out:
        raise FeedEmpty("amazon units: no row of the window belongs to a tracked product; nothing written")
    return out


# ---------------------------------------------------------------------------------------------------------------- new-customer revenue by product (Shopify)

def product_of(sku: str | None) -> str:
    s = sku or ""
    if s.startswith("LV-VBULL"):
        return "VitaBull"
    if s.startswith("LV-BLC180") or s == "DPCD1-GN180001":
        return "Beef Liver"
    if s.startswith("DPCD1-GN187001"):
        return "Boneflex"
    if s.startswith("LV-VBELLE"):
        return "Vital Belle"
    if s == "DPCD1-GN179001":
        return "Vital Flow"
    return "Other"


# The customer key is shopify_customer_id, else lower(email) (an empty string counts as missing); each customer's first order is the earliest
# (processed_at, id). processed_at is a UTC timestamp; the day is its Pacific date. Only first orders whose Pacific day is in the window are returned.
FIRST_ORDERS_SQL = """
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
    SELECT o.id,
           ((f.processed_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/Los_Angeles')::date AS pt_day,
           o.total_price,
           o.raw_data->>'test' AS test,
           (SELECT jsonb_agg(jsonb_build_object('sku', li->>'sku', 'p', li->>'price', 'q', li->>'quantity', 'd', li->>'total_discount'))
              FROM jsonb_array_elements(o.raw_data->'line_items') li) AS lines
    FROM firsts f
    JOIN shopify_orders o ON o.id = f.id
    WHERE ((f.processed_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/Los_Angeles')::date BETWEEN %(since)s AND %(until)s
    ORDER BY 2, 1
"""


def nc_rows(order_rows: list[tuple]) -> list[tuple]:
    """First orders (id, pacific day, total_price, test flag, line items) -> (day, product, nc_rev, nc_orders) rows, as the desktop job writes the tab."""
    out: dict[tuple, list[float]] = collections.defaultdict(lambda: [0.0, 0])
    for _oid, day, total, test, lines in order_rows:
        if test == "true" or not lines:
            continue
        net: dict[str, float] = collections.defaultdict(float)
        for li in lines:
            v = max(float(li.get("p") or 0) * float(li.get("q") or 0) - float(li.get("d") or 0), 0)
            net[product_of(li.get("sku"))] += v
        sn = sum(net.values())
        if sn <= 0:
            continue
        prim = max(net, key=net.get)                                             # the largest line, chosen before the roll-up into Other
        for p, v in net.items():
            out[(day, p)][0] += float(total) * v / sn
        out[(day, prim)][1] += 1
    merged: dict[tuple, list[float]] = collections.defaultdict(lambda: [0.0, 0])
    for (day, p), (rev, n) in out.items():
        p2 = p if p in NC_PRODUCTS else "Other"                                 # Vital Belle / Vital Flow / unmatched lines roll into Other
        merged[(day, p2)][0] += rev
        merged[(day, p2)][1] += n
    return [(d, p, round2(v[0]), float(v[1])) for (d, p), v in sorted(merged.items(), key=lambda kv: (kv[0][0], NC_PRODUCTS.index(kv[0][1])))]


def pull_nc_product(ctx: Ctx, span: Span) -> list[tuple]:
    rows = get_warehouse(ctx).query(FIRST_ORDERS_SQL, {"since": span.since, "until": span.until})
    if not rows:
        raise FeedEmpty("nc_product: no first orders in the window; nothing written")
    out = nc_rows(rows)
    if not out:
        raise FeedEmpty("nc_product: no priced first order in the window; nothing written")
    return out


# ---------------------------------------------------------------------------------------------------------------- sheet parser (units)

def sheet_rows_units_amazon(grid: list[list]) -> list[tuple]:
    from etl.projection_inventory_demand import parse_product_daily
    return [(r.location, r.product, r.day, r.units, r.source, r.is_estimate) for r in parse_product_daily(grid).rows]


# ---------------------------------------------------------------------------------------------------------------- feeds

# Tolerances absorb late-shipped orders (a B2B order can post to an old purchase day for about two weeks) and the nine-hour gap between the sheet's daily
# write and this pull; the definitions themselves were proven identical to the desktop SQL run at the same instant (see the dry-run notes in MODULE_feeds.md).
_MONEY = dict(abs_tol=50.0, rel_tol=0.01)
_COUNT = dict(abs_tol=2.0, rel_tol=0.02)


def _amazon_units_where(span: Span):
    """The window of the shared units table that this feed owns: its own location only (Revamp and international rows are other feeds')."""
    return sql.SQL("location = %s AND day >= %s"), ("amazon", span.since)


FEEDS = [
    Feed("amazon", "Amazon sales and ads (ExpandFi)", "expandfi", "proj_feed_amazon", "proj_feedx_amazon", AMAZON_COLS, ("day",), "day",
         pull=pull_amazon, days=21, stale_after=3, settle_days=14,
         metrics=(Metric("orders", **_COUNT), Metric("units", **_COUNT), Metric("revenue", **_MONEY), Metric("nc_orders", **_COUNT), Metric("nc_rev", **_MONEY),
                  Metric("ad_spend", 5.0, 0.005), Metric("attributed_sales", 5.0, 0.005), Metric("clicks", 5.0, 0.005), Metric("impressions", 200.0, 0.005)),
         sheet_tab="Amazon - Daily", sheet_parser=("etl.projection_pacing", "parse_amazon"),
         notes="Ads sales_14d matures for 14 days and late orders arrive for about two weeks, so the last 14 days are compared at 3x tolerance and never failed; "
               "the 21-day pull keeps days 15-21 (matured) refreshed for the strict comparison."),
    Feed("nc_product", "New-customer revenue by product (ExpandFi)", "expandfi", "proj_feed_nc_product", "proj_feedx_nc_product",
         ("day", "product", "nc_rev", "nc_orders"), ("day", "product"), "day", pull=pull_nc_product, days=14, stale_after=2, settle_days=2,
         metrics=(Metric("nc_rev", **_MONEY), Metric("nc_orders", **_COUNT)),
         sheet_tab="NC Rev - By Product", sheet_parser=("etl.projection_pacing", "parse_nc_product"),
         notes="Actual Shopify first orders, not Meta-attributed; Pacific days."),
    Feed("units_amazon", "Amazon units sold by product (ExpandFi)", "expandfi", "proj_units_daily", "proj_feedx_units_daily", UNITS_COLS,
         ("location", "product", "day"), "day", pull=pull_units_amazon, days=21, stale_after=3, settle_days=14,
         delete_where=_amazon_units_where, scope_where="location = 'amazon'", metrics=(Metric("units", **_COUNT),),
         sheet_tab="Amazon - Product Daily", sheet_parser=("etl.feeds.expandfi_feeds", "sheet_rows_units_amazon"),
         notes="Same pull as the sheet's Amazon - Product Daily: quantity_shipped by purchase day and product; 'Other' not stored."),
]
