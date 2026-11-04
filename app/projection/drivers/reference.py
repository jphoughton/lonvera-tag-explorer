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
    aov_window: dict | None = None                       # {aov, from, to, days}: new-customer revenue per new customer over the same window
    channels: tuple = ("meta", "google")
    unavailable: dict = field(default_factory=dict)      # 'cpa' | 'aov' | 'amazon' -> plain reason the number is not offered (feed gap, or outside the sanity range)


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
_NC_REV_W = "SELECT sum(nc_rev) AS v, sum(nc_orders) AS n, count(DISTINCT day) AS d FROM proj_feed_orders WHERE day BETWEEN %s AND %s"
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


FEED_LABELS = {"meta": "Meta spend feed", "google": "Google spend feed", "tiktok": "TikTok spend feed", "amazon": "Amazon ads feed", "bing": "Bing ads"}


def _limits(levers) -> dict:
    """Guard settings from the drivers levers (defaults when none are passed): the most feed days that may be missing from the window, and the
    range a believable realized CPA and AOV fall in."""
    from .model import Levers
    lv = levers if levers is not None else Levers()
    return {"gap": lv.reference_gap_days, "cpa": (lv.reference_cpa_min, lv.reference_cpa_max), "aov": (lv.reference_aov_min, lv.reference_aov_max)}


def load_reference(conn, channels: tuple, window_days: int, today: date, levers=None) -> Reference:
    """Feed references for the Spend plan page and the payback defaults. A number is offered only when its feeds cover the window and the result is
    believable; otherwise it is None and `unavailable[...]` says why in plain words, so every caller can show the reason and fall back."""
    ref = Reference(window_days=window_days, channels=tuple(channels))
    lim = _limits(levers)
    need = window_days - lim["gap"]
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
            if n >= need:
                ref.amazon_window = {"daily": v / n, "from": w[0].isoformat(), "to": w[1].isoformat(), "days": n}
            elif n:
                ref.unavailable["amazon"] = f"Amazon feed has {n} of {window_days} days"

    # ---- realized CPA and AOV
    if not have_orders:
        ref.unavailable["cpa"] = ref.unavailable["aov"] = "Shopify new-customer feed is not loaded"
        return ref
    orders = _month_sums(conn, _NC_M, start)
    last_orders = conn.execute("SELECT max(day) AS d FROM proj_feed_orders WHERE nc_orders > 0").fetchone()["d"]
    w = _window(last_orders, window_days, today)
    if w is None:
        ref.unavailable["cpa"] = ref.unavailable["aov"] = "Shopify new-customer feed has no data"
        return ref

    # sources of ad spend: every channel the plan counts must have a feed that covers the window
    sources: dict[str, tuple] = {}
    for ch in channels:
        if ch == "meta" and have_meta:
            sources[ch] = (_month_sums(conn, _META_M, start), lambda a, b: _window_sum(conn, _META_W, a, b))
        elif ch in PLATFORM_NAMES and have_platform:
            name = PLATFORM_NAMES[ch]
            sources[ch] = (_month_sums(conn, _PLAT_M, name, start), lambda a, b, name=name: _window_sum(conn, _PLAT_W, name, a, b))
        elif ch == "amazon" and have_amazon:
            sources[ch] = (_month_sums(conn, _AMZ_AD_M, start), lambda a, b: _window_sum(conn, _AMZ_AD_W, a, b))
    nc, n_nc = _window_sum(conn, _NC_W, *w)
    reason = None
    if n_nc < need:
        reason = f"Shopify new-customer feed has {n_nc} of {window_days} days"
    spend_w: dict[str, tuple[float, int]] = {}
    for ch in channels:
        if reason:
            break
        if ch not in sources:
            reason = f"{FEED_LABELS.get(ch, ch)} is not loaded"
            break
        spend_w[ch] = sources[ch][1](*w)
        if spend_w[ch][1] < need:
            reason = f"{FEED_LABELS.get(ch, ch)} has {spend_w[ch][1]} of {window_days} days"
    if reason is None:
        total = sum(v for v, _ in spend_w.values())
        if nc <= 0 or total <= 0:
            reason = "The feeds hold no spend or no new customers in the window"
        else:
            cpa = total / nc
            lo, hi = lim["cpa"]
            if not lo <= cpa <= hi:
                reason = f"Realized CPA ${cpa:,.2f} is outside the ${lo:,.0f} to ${hi:,.0f} a real feed gives"
            else:
                ref.cpa_window = {"cpa": cpa, "spend": total, "new_customers": nc, "from": w[0].isoformat(), "to": w[1].isoformat(), "days": n_nc}
    if reason:
        ref.unavailable["cpa"] = reason

    # AOV over the same window (needs only the orders feed)
    rev, orders_n, d = _window_sum3(conn, w)
    if d < need:
        ref.unavailable["aov"] = f"Shopify new-customer feed has {d} of {window_days} days"
    elif orders_n <= 0 or rev <= 0:
        ref.unavailable["aov"] = "The orders feed holds no new-customer revenue in the window"
    else:
        aov = rev / orders_n
        lo, hi = lim["aov"]
        if lo <= aov <= hi:
            ref.aov_window = {"aov": aov, "from": w[0].isoformat(), "to": w[1].isoformat(), "days": d}
        else:
            ref.unavailable["aov"] = f"Realized AOV ${aov:,.2f} is outside the ${lo:,.0f} to ${hi:,.0f} a real feed gives"

    # by closed month (complete months only, and only believable values)
    lo, hi = lim["cpa"]
    if sources and all(ch in sources for ch in channels):
        for m, (nc_m, n_m) in orders.items():
            if m >= cutoff or n_m < _days_in(m) or nc_m <= 0:
                continue
            total, ok = 0.0, True
            for ch, (monthly, _) in sources.items():
                v, n = monthly.get(m, (0.0, 0))
                if n < _days_in(m):
                    ok = False
                total += v
            if ok and total > 0 and lo <= total / nc_m <= hi:
                ref.cpa_by_month[m] = total / nc_m
    return ref


def _window_sum3(conn, w) -> tuple[float, float, int]:
    r = conn.execute(_NC_REV_W, w).fetchone()
    return float(r["v"] or 0), float(r["n"] or 0), int(r["d"])


def amazon_series(ref: Reference, today: date) -> dict[Month, float]:
    """The reference row: closed months the feed covers completely, then the run-rate for the open month on."""
    out = dict(ref.amazon_by_month)
    if ref.amazon_window:
        for m in MONTHS:
            if m >= today.replace(day=1):
                out[m] = ref.amazon_window["daily"] * AVG_DAYS_PER_MONTH
    return out
