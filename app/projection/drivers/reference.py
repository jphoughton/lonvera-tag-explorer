"""Read-only feed references shown beside the plan inputs: what customers really cost lately, and what the Amazon feed says.

    load_reference(conn, channels, window_days, today) -> Reference

  realized CPA   ad spend of the channels chosen in Plan settings (Meta from ad_daily, Google and TikTok from the ad-platform feed, Amazon ads from the
                 Amazon feed) divided by Shopify new customers (orders feed), by closed month and over the last `window_days` complete days.
  Amazon feed    revenue from the Amazon feed: the monthly total for closed months the feed covers completely, and the run-rate (daily average over the last
                 `window_days` complete days x the average month length) for the open and later months.

The newest day of a feed is dropped from its window (the newest day is usually partial). A feed table that does not exist or holds nothing gives an
empty reference; nothing here raises on missing data and nothing is written.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..interfaces import MONTHS, Month, add_months

PLATFORM_NAMES = {"google": "Google Ads", "tiktok": "TikTok Ads"}
AVG_DAYS_PER_MONTH = 365.25 / 12                  # a typed monthly plan does not follow month length, so neither does the run-rate


@dataclass
class Reference:
    window_days: int = 28
    cpa_by_month: dict[Month, float] = field(default_factory=dict)
    cpa_window: dict | None = None                       # {cpa, spend, new_customers, from, to, days}
    amazon_by_month: dict[Month, float] = field(default_factory=dict)      # closed months fully covered by the feed
    amazon_window: dict | None = None                    # {daily, from, to, days}
    channels: tuple = ("meta", "google")


def _has(conn, name: str) -> bool:
    return bool(conn.execute("SELECT to_regclass(%s) AS t", (name,)).fetchone()["t"])


def _month_sums(conn, sql: str, *args) -> dict[Month, tuple[float, int]]:
    out: dict[Month, tuple[float, int]] = {}
    for r in conn.execute(sql, args).fetchall():
        out[r["m"]] = (float(r["v"] or 0), int(r["n"]))
    return out


def _window_sum(conn, sql: str, *args) -> tuple[float, int]:
    r = conn.execute(sql, args).fetchone()
    return float(r["v"] or 0), int(r["n"])


# fixed SQL per source (no string-built SQL: every value is a bound parameter)
_NC_M = "SELECT date_trunc('month', day)::date AS m, sum(nc_orders) AS v, count(DISTINCT day) AS n FROM proj_feed_orders WHERE day >= %s GROUP BY 1"
_NC_W = "SELECT sum(nc_orders) AS v, count(DISTINCT day) AS n FROM proj_feed_orders WHERE day BETWEEN %s AND %s"
_META_M = "SELECT date_trunc('month', day)::date AS m, sum(spend) AS v, count(DISTINCT day) AS n FROM ad_daily WHERE day >= %s GROUP BY 1"
_META_W = "SELECT sum(spend) AS v, count(DISTINCT day) AS n FROM ad_daily WHERE day BETWEEN %s AND %s"
_PLAT_M = "SELECT date_trunc('month', day)::date AS m, sum(spend) AS v, count(DISTINCT day) AS n FROM proj_feed_platform WHERE platform = %s AND day >= %s GROUP BY 1"
_PLAT_W = "SELECT sum(spend) AS v, count(DISTINCT day) AS n FROM proj_feed_platform WHERE platform = %s AND day BETWEEN %s AND %s"
_AMZ_AD_M = "SELECT date_trunc('month', day)::date AS m, sum(ad_spend) AS v, count(DISTINCT day) AS n FROM proj_feed_amazon WHERE day >= %s GROUP BY 1"
_AMZ_AD_W = "SELECT sum(ad_spend) AS v, count(DISTINCT day) AS n FROM proj_feed_amazon WHERE day BETWEEN %s AND %s"
_AMZ_REV_M = "SELECT date_trunc('month', day)::date AS m, sum(revenue) AS v, count(DISTINCT day) AS n FROM proj_feed_amazon WHERE day >= %s GROUP BY 1"
_AMZ_REV_W = "SELECT sum(revenue) AS v, count(DISTINCT day) AS n FROM proj_feed_amazon WHERE day BETWEEN %s AND %s"


def _days_in(m: Month) -> int:
    return calendar.monthrange(m.year, m.month)[1]


def _window(last_day: date | None, n: int, today: date) -> tuple[date, date] | None:
    """The `n` complete days ending the day before the feed's newest day (never later than yesterday)."""
    if last_day is None:
        return None
    end = min(last_day - timedelta(days=1), today - timedelta(days=1))
    return end - timedelta(days=n - 1), end


def load_reference(conn, channels: tuple, window_days: int, today: date) -> Reference:
    ref = Reference(window_days=window_days, channels=tuple(channels))
    start = MONTHS[0]
    cutoff = today.replace(day=1)
    have_orders, have_meta = _has(conn, "proj_feed_orders"), _has(conn, "ad_daily")
    have_platform, have_amazon = _has(conn, "proj_feed_platform"), _has(conn, "proj_feed_amazon")

    # ---- Amazon feed
    if have_amazon:
        last = conn.execute("SELECT max(day) AS d FROM proj_feed_amazon WHERE revenue > 0").fetchone()["d"]
        for m, (v, n) in _month_sums(conn, _AMZ_REV_M, start).items():
            if m < cutoff and n >= _days_in(m):
                ref.amazon_by_month[m] = v
        w = _window(last, window_days, today)
        if w:
            v, n = _window_sum(conn, _AMZ_REV_W, *w)
            if n:
                ref.amazon_window = {"daily": v / n, "from": w[0].isoformat(), "to": w[1].isoformat(), "days": n}

    # ---- realized CPA
    if have_orders:
        orders = _month_sums(conn, _NC_M, start)
        spend_m: dict[str, dict[Month, tuple[float, int]]] = {}
        spend_w: dict[str, tuple] = {}
        last_orders = conn.execute("SELECT max(day) AS d FROM proj_feed_orders WHERE nc_orders > 0").fetchone()["d"]
        w = _window(last_orders, window_days, today)

        def add_source(ch: str, monthly, window) -> None:
            spend_m[ch] = monthly
            if w:
                spend_w[ch] = window(*w)

        for ch in channels:
            if ch == "meta" and have_meta:
                add_source(ch, _month_sums(conn, _META_M, start), lambda a, b: _window_sum(conn, _META_W, a, b))
            elif ch in PLATFORM_NAMES and have_platform:
                name = PLATFORM_NAMES[ch]
                add_source(ch, _month_sums(conn, _PLAT_M, name, start), lambda a, b, name=name: _window_sum(conn, _PLAT_W, name, a, b))
            elif ch == "amazon" and have_amazon:
                add_source(ch, _month_sums(conn, _AMZ_AD_M, start), lambda a, b: _window_sum(conn, _AMZ_AD_W, a, b))
        if spend_m:
            for m, (nc, n_nc) in orders.items():
                if m >= cutoff or n_nc < _days_in(m) or nc <= 0:
                    continue
                total, ok = 0.0, True
                for ch in spend_m:
                    v, n = spend_m[ch].get(m, (0.0, 0))
                    if ch == "meta" and n < _days_in(m):
                        ok = False
                    total += v
                if ok and total > 0:
                    ref.cpa_by_month[m] = total / nc
            if w:
                nc, n_nc = _window_sum(conn, _NC_W, *w)
                total = sum(v for v, _ in spend_w.values())
                if nc > 0 and total > 0 and n_nc >= window_days - 1:
                    ref.cpa_window = {"cpa": total / nc, "spend": total, "new_customers": nc, "from": w[0].isoformat(), "to": w[1].isoformat(), "days": n_nc}
    return ref


def amazon_series(ref: Reference, today: date) -> dict[Month, float]:
    """The reference row: closed months the feed covers completely, then the run-rate for the open month on."""
    out = dict(ref.amazon_by_month)
    if ref.amazon_window:
        for m in MONTHS:
            if m >= today.replace(day=1):
                out[m] = ref.amazon_window["daily"] * AVG_DAYS_PER_MONTH
    return out
