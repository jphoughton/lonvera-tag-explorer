"""Drivers engine: the spend plan -> new-customer (NC) revenue, plus the Amazon / TikTok revenue plan. Pure functions, no database.

Replaces P&L rows 11-16 and 51-56 and the plan inputs of rows 19-20 of the Lonvera Projections sheet.

    compute_drivers(params, inputs) -> DriversResult

Per month:
  * an ACTUAL month is a closed month with loaded spend (QuickBooks channel lines, else the Meta feed). Its channel spend is the loaded value.
  * every other month is PLANNED: total marketing spend is the typed cell (else the previous month's value carried forward), split over the
    channels by the `channel_share` lever. Ad spend (the NC denominator, P&L row 11) is Meta + Google.
  * NC orders / NC revenue come, in this order, from a typed override, from the cohort table (closed months), from the plan:
    orders = ad spend / cost per acquisition (CPA); revenue = orders x average order value (AOV).
  * CPA and AOV are typed per month, else the previous month's effective value carries forward; an optional NC-ROAS slide (lever) sets
    CPA = AOV / ROAS for months with no typed CPA.
Repeat revenue, DTC revenue, sales tax and total company revenue need the cohort curve and live in revenue.py (called after the cohorts module).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..interfaces import MONTHS, DriverResult, Month, Series, month_floor
from .payback import HORIZON

SHARE_KEYS = ("amazon", "meta", "google", "bing", "tiktok")              # lever keys
# P&L B52:B56 (trailing actual mix, snapshot 2026-11-03)
DEFAULT_SHARES = {"amazon": 0.04920832435847688, "meta": 0.8971495015118965, "google": 0.048089806249984754, "bing": 0.0,
                  "tiktok": 0.005552367879641713}
DEFAULT_SALES_TAX = 0.07
DEFAULT_PAYBACK = {"product_cost_pct": 0.1726, "fulfillment_per_order": 5.53, "horizon_months": HORIZON}   # Payback tab D11, B12, and the 36-month table (E20:E56)
DEFAULT_AD_CHANNELS = ("meta", "google")                    # P&L row 11 = Facebook + Google (the new-customer denominator)
DEFAULT_POSTURE_BAND = 0.10                                 # next-window spend vs the window before: beyond +-10% reads as growth / harvest (app default)
DEFAULT_SUMMARY_MONTHS = 12                                 # length of the headline windows (app default)

PLAN_KEYS = ("spend_total", "nc_cpa", "nc_aov", "amazon_rev", "tiktok_rev")        # typed plan inputs (proj_series 'drivers.<key>', kind input)
OVERRIDE_KEYS = ("ad_spend", "nc_orders", "nc_rev", "repeat_rev")                  # typed over a computed value (kind override)
SERIES_PREFIX = "drivers."
QB_CHANNEL_KEYS = {"amazon": ("pnl.mkt_amazon_ads", "spend.amazon_ads"), "meta": ("pnl.mkt_facebook_ads", "spend.meta"),
                   "google": ("pnl.mkt_google_ads", "spend.google"), "bing": ("pnl.mkt_bing_ads", "spend.bing"),
                   "tiktok": ("pnl.mkt_tiktok_ads", "spend.tiktok")}
META_FEED_KEY = "spend.meta_feed"
REVENUE_KEYS = {"amazon_rev": ("pnl.amazon_rev", "amazon_rev"), "tiktok_rev": ("pnl.tiktok_rev", "tiktok_rev"), "dtc_rev": ("pnl.dtc_rev", "dtc_rev")}
NC_ACTUAL_KEYS = ("nc.orders", "nc.rev")


# ---------------------------------------------------------------------------------------------------------------- levers

@dataclass
class RoasSlide:
    enabled: bool = False
    start_month: Month | None = None
    end_month: Month | None = None
    start: float = 0.0
    end: float = 0.0

    def covers(self, m: Month) -> bool:
        """True for the months the slide governs (start_month through end_month); later months only hold the end value where no CPA is typed."""
        return bool(self.enabled and self.start_month and self.end_month and self.start_month <= m <= self.end_month and self.start > 0 and self.end > 0)

    def roas(self, m: Month) -> float | None:
        """NC-ROAS for month m: `start` at start_month, linear to `end` at end_month, then held at `end`. None before start_month or when off."""
        if not self.enabled or self.start_month is None or self.end_month is None or m < self.start_month or self.start <= 0 or self.end <= 0:
            return None
        if m >= self.end_month or self.end_month <= self.start_month:
            return self.end
        span = (self.end_month.year - self.start_month.year) * 12 + self.end_month.month - self.start_month.month
        k = (m.year - self.start_month.year) * 12 + m.month - self.start_month.month
        return self.start + (self.end - self.start) * k / span


@dataclass
class Levers:
    shares: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SHARES))
    sales_tax_rate: float = DEFAULT_SALES_TAX
    roas_slide: RoasSlide = field(default_factory=RoasSlide)
    payback: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PAYBACK))
    ad_channels: tuple = DEFAULT_AD_CHANNELS
    posture_band: float = DEFAULT_POSTURE_BAND
    summary_months: int = DEFAULT_SUMMARY_MONTHS

    def to_dict(self) -> dict:
        rs = self.roas_slide
        return {"channel_share": dict(self.shares), "sales_tax_rate": self.sales_tax_rate,
                "roas_slide": {"enabled": rs.enabled, "from": rs.start_month.isoformat() if rs.start_month else None,
                               "to": rs.end_month.isoformat() if rs.end_month else None, "start": rs.start, "end": rs.end},
                "payback": dict(self.payback), "ad_spend_channels": list(self.ad_channels), "posture_band": self.posture_band,
                "summary_months": self.summary_months}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _month(v: Any) -> Month | None:
    if isinstance(v, date):
        return v
    try:
        d = date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None
    return d if d is not None and d.day == 1 else None


def parse_levers(params: dict | None) -> Levers:
    """Lever values from `params['drivers']` (the scenario's effective params). Anything missing or malformed falls back to the default."""
    p = (params or {}).get("drivers")
    p = p if isinstance(p, dict) else {}
    lv = Levers()
    cs = p.get("channel_share")
    if isinstance(cs, dict):
        for k in SHARE_KEYS:
            v = _num(cs.get(k))
            if v is not None and 0 <= v <= 1:
                lv.shares[k] = v
    v = _num(p.get("sales_tax_rate"))
    if v is not None and 0 <= v <= 0.5:
        lv.sales_tax_rate = v
    rs = p.get("roas_slide")
    if isinstance(rs, dict):
        s, e = _num(rs.get("start")), _num(rs.get("end"))
        a, b = _month(rs.get("from")), _month(rs.get("to"))
        if bool(rs.get("enabled")) and s and e and s > 0 and e > 0 and a and b:
            lv.roas_slide = RoasSlide(True, a, b, s, e)
    pb = p.get("payback")
    if isinstance(pb, dict):
        c, f = _num(pb.get("product_cost_pct")), _num(pb.get("fulfillment_per_order"))
        if c is not None and 0 <= c < 1:
            lv.payback["product_cost_pct"] = c
        if f is not None and f >= 0:
            lv.payback["fulfillment_per_order"] = f
        hz = _num(pb.get("horizon_months"))
        if hz is not None and 1 <= hz <= 60 and hz == int(hz):
            lv.payback["horizon_months"] = int(hz)
    ch = p.get("ad_spend_channels")
    if isinstance(ch, list) and ch and all(c in SHARE_KEYS for c in ch):
        lv.ad_channels = tuple(dict.fromkeys(ch))
    band = _num(p.get("posture_band"))
    if band is not None and 0 < band <= 1:
        lv.posture_band = band
    n = _num(p.get("summary_months"))
    if n is not None and 1 <= n <= 36 and n == int(n):
        lv.summary_months = int(n)
    return lv


# ---------------------------------------------------------------------------------------------------------------- inputs / result

@dataclass
class DriverInputs:
    """Everything compute_drivers reads. Built by loaders.load_inputs; tests build it directly."""
    today: date = field(default_factory=date.today)
    plan: dict[str, Series] = field(default_factory=dict)             # PLAN_KEYS -> typed scenario cells
    overrides: dict[str, Series] = field(default_factory=dict)        # OVERRIDE_KEYS -> typed overrides
    cell_info: dict[str, dict[Month, dict]] = field(default_factory=dict)   # key -> month -> {kind, note, origin}
    qb_spend: dict[str, Series] = field(default_factory=dict)         # SHARE_KEYS -> loaded books (QuickBooks) spend by channel
    meta_feed: Series = field(default_factory=dict)                   # complete months of Meta spend rolled in from ad_daily
    amazon_actual: Series = field(default_factory=dict)
    tiktok_actual: Series = field(default_factory=dict)
    dtc_actual: Series = field(default_factory=dict)
    nc_orders_actual: Series = field(default_factory=dict)            # closed cohort months: new customers
    nc_rev_actual: Series = field(default_factory=dict)               # closed cohort months: new customers x first-order $
    nc_source: str = ""


@dataclass
class DriversResult(DriverResult):
    """DriverResult (the interchange fields) plus what the pnl / cohorts / UI need. Series are over the 60-month axis unless noted."""
    spend_bing: Series = field(default_factory=dict)
    marketing_spend: Series = field(default_factory=dict)           # P&L row 51 (all channels) = spend_total
    ad_spend: Series = field(default_factory=dict)                  # P&L row 11: the ad_spend_channels lever's channels (Meta + Google by default), or the typed override
    plan_cpa: Series = field(default_factory=dict)                  # the effective CPA lever (typed, slid, carried, or implied for actual months)
    plan_aov: Series = field(default_factory=dict)
    sales_tax_rate: float = DEFAULT_SALES_TAX
    repeat_override: Series = field(default_factory=dict)           # typed repeat revenue (P&L row 17 hand-typed months)
    dtc_actual: Series = field(default_factory=dict)                # loaded DTC revenue (months where the sales-tax estimate is not applied)
    nc_rev_cohort: Series = field(default_factory=dict)             # NC revenue implied by the cohort table, for comparison with overrides
    source: dict[Month, str] = field(default_factory=dict)          # 'books' | 'meta_feed' | 'plan'
    carried: dict[str, set] = field(default_factory=dict)           # key -> months whose value was carried forward (not typed)
    slid: set = field(default_factory=set)                          # months whose CPA came from the NC-ROAS slide
    nc_basis: dict[Month, str] = field(default_factory=dict)        # 'override' | 'cohort' | 'plan'
    meta_books: Series = field(default_factory=dict)                # Meta spend from the books (for the feed gap)
    meta_feed: Series = field(default_factory=dict)
    levers: Levers = field(default_factory=Levers)
    cell_info: dict[str, dict[Month, dict]] = field(default_factory=dict)     # typed cells: key -> month -> {kind, note, origin}
    nc_source: str = ""
    overrides: dict[str, Series] = field(default_factory=dict)               # typed overrides (ad_spend, nc_orders, nc_rev, repeat_rev)
    # filled by revenue.revenue_lines()
    repeat_rev: Series = field(default_factory=dict)
    dtc_rev: Series = field(default_factory=dict)
    total_revenue: Series = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------------------- compute

def _pos(v: float | None) -> bool:
    return v is not None and v > 0


def compute_drivers(params: dict | None, inp: DriverInputs) -> DriversResult:
    lv = parse_levers(params)
    sh = lv.shares
    cutoff = month_floor(inp.today)                          # months before this are closed
    books_months = {m for s in inp.qb_spend.values() for m in s}
    feed_months = set(inp.meta_feed)
    actual_months = {m for m in (books_months | feed_months) if m < cutoff}
    plan, ovr = inp.plan, inp.overrides
    res = DriversResult(levers=lv, sales_tax_rate=lv.sales_tax_rate, meta_feed=dict(inp.meta_feed), meta_books=dict(inp.qb_spend.get("meta", {})),
                        cell_info=inp.cell_info, nc_source=inp.nc_source, overrides={k: dict(v) for k, v in ovr.items()})
    res.repeat_override = dict(ovr.get("repeat_rev", {}))
    res.dtc_actual = {m: v for m, v in inp.dtc_actual.items() if m < cutoff}
    res.carried = {k: set() for k in PLAN_KEYS}
    last = {"spend_total": 0.0, "nc_cpa": 0.0, "nc_aov": 0.0, "amazon_rev": 0.0, "tiktok_rev": 0.0}
    seen_last = {k: False for k in last}

    for m in MONTHS:
        # ---- spend by channel
        if m in actual_months:
            if m in books_months:
                chan = {k: inp.qb_spend.get(k, {}).get(m, 0.0) for k in SHARE_KEYS}
                res.source[m] = "books"
            else:                                            # Meta feed only: Meta is exact, the other channels follow the lever mix
                meta = inp.meta_feed[m]
                total_est = meta / sh["meta"] if sh["meta"] > 0 else meta
                chan = {k: total_est * sh[k] for k in SHARE_KEYS}
                chan["meta"] = meta
                res.source[m] = "meta_feed"
            total = sum(chan.values())
        else:
            res.source[m] = "plan"
            typed = plan.get("spend_total", {}).get(m)
            if typed is not None:
                total = typed
            else:
                total = last["spend_total"]
                if seen_last["spend_total"]:
                    res.carried["spend_total"].add(m)
            chan = {k: total * sh[k] for k in SHARE_KEYS}
        last["spend_total"], seen_last["spend_total"] = total, True
        res.spend_total[m] = res.marketing_spend[m] = total
        res.spend_amazon_ads[m], res.spend_meta[m], res.spend_google[m] = chan["amazon"], chan["meta"], chan["google"]
        res.spend_bing[m], res.spend_tiktok[m] = chan["bing"], chan["tiktok"]
        ad = ovr.get("ad_spend", {}).get(m)
        ad = sum(chan[k] for k in lv.ad_channels) if ad is None else ad
        res.ad_spend[m] = ad

        # ---- new customers
        closed = m < cutoff
        coh_o = inp.nc_orders_actual.get(m) if closed else None
        coh_r = inp.nc_rev_actual.get(m) if closed else None
        if coh_r is not None:
            res.nc_rev_cohort[m] = coh_r
        o_ov, r_ov = ovr.get("nc_orders", {}).get(m), ovr.get("nc_rev", {}).get(m)

        aov_in = plan.get("nc_aov", {}).get(m)
        aov = aov_in if aov_in is not None else last["nc_aov"]
        aov_carried = aov_in is None and seen_last["nc_aov"]
        cpa_in = plan.get("nc_cpa", {}).get(m)
        slid = False
        # inside the slide window a typed CPA only counts when it is an override; outside it (or with the slide off) every typed CPA counts
        if cpa_in is not None and lv.roas_slide.covers(m) and inp.cell_info.get("nc_cpa", {}).get(m, {}).get("kind") != "override":
            cpa_in = None
        if cpa_in is not None:
            cpa = cpa_in
        else:
            roas = lv.roas_slide.roas(m)
            if roas and aov > 0:
                cpa, slid = aov / roas, True
            else:
                cpa = last["nc_cpa"]
        cpa_carried = cpa_in is None and not slid and seen_last["nc_cpa"]

        orders_planned = o_ov is None and coh_o is None
        orders = o_ov if o_ov is not None else coh_o if coh_o is not None else (ad / cpa if cpa > 0 else 0.0)
        rev_planned = r_ov is None and coh_r is None
        rev = r_ov if r_ov is not None else coh_r if coh_r is not None else orders * aov
        res.nc_orders[m], res.nc_rev[m] = orders, rev
        res.nc_basis[m] = "override" if (o_ov is not None or r_ov is not None) else "cohort" if (coh_o is not None or coh_r is not None) else "plan"
        res.nc_cpa[m] = ad / orders if orders > 0 else 0.0
        res.nc_aov[m] = rev / orders if orders > 0 else 0.0
        res.nc_roas[m] = rev / ad if ad > 0 else 0.0

        # effective levers (what the next month carries): the plan value when the month was planned, the implied value when it was measured
        eff_cpa = cpa if orders_planned else (res.nc_cpa[m] if orders > 0 else last["nc_cpa"])
        eff_aov = aov if rev_planned else (res.nc_aov[m] if orders > 0 else last["nc_aov"])
        res.plan_cpa[m], res.plan_aov[m] = eff_cpa, eff_aov
        if slid and orders_planned:
            res.slid.add(m)
        if cpa_carried and orders_planned:
            res.carried["nc_cpa"].add(m)
        if aov_carried and rev_planned:
            res.carried["nc_aov"].add(m)
        last["nc_cpa"], seen_last["nc_cpa"] = eff_cpa, True
        last["nc_aov"], seen_last["nc_aov"] = eff_aov, True

        # ---- Amazon and TikTok revenue: loaded actuals for closed months, else the typed plan, else carried
        for key, actual in (("amazon_rev", inp.amazon_actual), ("tiktok_rev", inp.tiktok_actual)):
            if closed and m in actual:
                v = actual[m]
            else:
                typed = plan.get(key, {}).get(m)
                v = typed if typed is not None else last[key]
                if typed is None and seen_last[key]:
                    res.carried[key].add(m)
            last[key], seen_last[key] = v, True
            (res.amazon_rev if key == "amazon_rev" else res.tiktok_rev)[m] = v

    res.actual_through = max(actual_months) if actual_months else None
    return res


def month_range(a: Month, b: Month) -> list[Month]:
    """Axis months from a through b inclusive."""
    return [m for m in MONTHS if a <= m <= b]
