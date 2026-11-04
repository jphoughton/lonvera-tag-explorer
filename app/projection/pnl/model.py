"""P&L engine: revenue -> cost of goods sold -> gross profit -> marketing, other business expenses, headcount, overhead -> net operating income,
plus fiscal-year roll-ups, trailing and forward metrics, growth rates and implied enterprise value. Pure functions, no database.

How a month is computed
    actual    month <= qb_through (the last month with loaded QuickBooks data): every leaf line is its loaded value (blank = no data, counted as 0),
              parents are sums of their children.
    projected every other month: each leaf line follows its driver (see `Segment` modes) on top of revenue from the drivers and cohorts modules,
              unless the scenario holds a monthly override for it (`pnl.<key>` in proj_series).

Driver modes (a driver is a list of segments ordered by "from"; the last segment on or before the month applies)
    fixed     {value}                          dollars per month
    carry                                      last month's value
    avg       {window}                         mean of the previous `window` months of the line
    rate      {base, rate_kind, window?, rate?}
                  base       amazon_rev | dtc_rev | total_revenue
                  window     sum of the line / sum of the base over the last N actual months (N = engine.rate_window_months unless given)
                  ratio_avg  mean of the monthly ratios over the last `window` actual months
                  typed      the `rate` given
                  cogs_model blended landed cost per bottle / price per bottle (the COGS model)
    headcount                                  base + step increments once annualised revenue passes a threshold, from a start month
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..interfaces import MONTHS, PnlResult, Series, add_months
from . import lines as L

# The one place the module carries starting values for its variables. They are the sheet's values; the seeder (etl/projection_pnl.py) writes the same
# groups into proj_pnl_settings with the sheet cell as the note, and a scenario overrides any of them in params['pnl'][group]. The engine uses this
# table only for a group that proj_pnl_settings does not hold, and says so in `warnings`. Nothing else in the module names a business number.
#   engine     rate window (P&L!R:U trailing sums), annual inflation (P&L!A4, A5)
#   headcount  P&L!W75: base + floor(max(0, annualised revenue - threshold) / step) x increment, from the start month
#   cogs       COGS!N24 US share, COGS!L38 price per bottle (forward), COGS!L37 current price; mix / china_cost / us_cost per SKU override the table
#   ev         P&L!C161:H162 multiples, BN100 basis year, A160 offer
#   trend      P&L!H145 etc.: months in the growth-rate averages
#   freshness  days after which the first month not loaded counts as late (global, not per scenario)
DEFAULT_SETTINGS: dict[str, dict] = {
    "engine": {"rate_window_months": 4, "opex_inflation": 0.0, "cogs_inflation": 0.0},
    "headcount": {"base": 15000.0, "threshold": 20_000_000.0, "step": 5_000_000.0, "increment": 15000.0, "start": "2026-09-01"},
    "cogs": {"us_share": 0.7, "price_per_bottle": 29.22, "current_price_per_bottle": 21.62, "mix": {}, "china_cost": {}, "us_cost": {}},
    "ev": {"multiples": [3, 4, 5, 6, 7, 8], "year": 2027, "loi": 7_500_000.0},
    "trend": {"avg_window": 6},
    "freshness": {"stale_after_days": 45},
}
SCENARIO_GROUPS = ("engine", "headcount", "cogs", "ev", "trend")        # settings a scenario may override (freshness is global)
BASES = ("amazon_rev", "dtc_rev", "total_revenue")
MODES = ("fixed", "carry", "avg", "rate", "headcount")
RATE_KINDS = ("window", "ratio_avg", "typed", "cogs_model")
COGS_GROUP = ("china_cogs", "us_cogs", "inventory_shrinkage")
MAX_WINDOW = 36


# ---------------------------------------------------------------------------------------------------------------- inputs

@dataclass
class PnlInputs:
    actuals: dict[str, Series] = field(default_factory=dict)       # line key -> month -> loaded QuickBooks value
    overrides: dict[str, Series] = field(default_factory=dict)     # line key -> month -> scenario override (projected months only)
    drivers: dict[str, list[dict]] = field(default_factory=dict)   # line key -> default segments (proj_pnl_drivers)
    settings: dict[str, dict] = field(default_factory=dict)        # defaults over DEFAULT_SETTINGS (proj_pnl_settings)
    cogs_sku: list[dict] = field(default_factory=list)             # proj_pnl_cogs_sku rows
    today: date | None = None                                      # actual months must be closed: before the first of this month


@dataclass
class PnlOutput(PnlResult):
    actual_through: date | None = None                              # last month with loaded QuickBooks data (the P&L's own actual boundary)
    window: list[date] = field(default_factory=list)               # months the trailing-rate drivers read
    driver_info: dict[str, dict] = field(default_factory=dict)     # leaf key -> resolved driver description for the driver table
    cogs_model: dict = field(default_factory=dict)                 # inputs and result of the COGS model
    settings: dict = field(default_factory=dict)                   # effective settings (defaults + scenario)
    warnings: list[str] = field(default_factory=list)
    fy_flat: dict[str, float] = field(default_factory=dict)        # '<year>.<line key>' -> fiscal-year value (the reconcile harness addresses cells by path)


# ---------------------------------------------------------------------------------------------------------------- settings and drivers

def deep_merge(base: dict, over: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def effective_settings(defaults: dict[str, dict], params: dict) -> dict[str, dict]:
    """DEFAULT_SETTINGS < table defaults < scenario params['pnl'][name]."""
    out = {name: dict(v) for name, v in DEFAULT_SETTINGS.items()}
    for name, v in (defaults or {}).items():
        out[name] = deep_merge(out.get(name, {}), v if isinstance(v, dict) else {})
    for name in SCENARIO_GROUPS:
        if isinstance(params.get(name), dict):
            out[name] = deep_merge(out[name], params[name])
    return out


def effective_drivers(defaults: dict[str, list[dict]], params: dict) -> dict[str, list[dict]]:
    """Table defaults, with each key the scenario params['pnl']['drivers'] carries replaced as a whole."""
    out = {k: list(v) for k, v in (defaults or {}).items()}
    for k, v in (params.get("drivers") or {}).items():
        segs = v.get("segments") if isinstance(v, dict) else v
        if isinstance(segs, list):
            out[k] = segs
    return out


def validate_segments(key: str, segments: Any) -> list[dict]:
    """Normalise and check one driver. ValueError with a message naming the problem."""
    if key not in L.DRIVER_KEYS:
        raise ValueError(f"{key!r} is not a driven line")
    if not isinstance(segments, list) or not segments or len(segments) > 12:
        raise ValueError("segments must be a list of 1-12 steps")
    out: list[dict] = []
    for i, s in enumerate(segments):
        where = f"step {i + 1}"
        if not isinstance(s, dict):
            raise ValueError(f"{where}: must be an object")
        mode = s.get("mode")
        if mode not in MODES:
            raise ValueError(f"{where}: mode must be one of {', '.join(MODES)}")
        frm = s.get("from")
        if frm is not None:
            try:
                d = date.fromisoformat(frm)
            except (TypeError, ValueError):
                raise ValueError(f"{where}: from must be a first-of-month date or empty") from None
            if d.day != 1:
                raise ValueError(f"{where}: from must be the first of a month")
        elif i > 0:
            raise ValueError(f"{where}: only the first step may start from the beginning")
        seg: dict = {"from": frm, "mode": mode}
        if mode == "fixed":
            v = s.get("value")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e9:
                raise ValueError(f"{where}: fixed needs a numeric value")
            seg["value"] = float(v)
        elif mode == "avg":
            w = s.get("window")
            if isinstance(w, bool) or not isinstance(w, int) or not 1 <= w <= MAX_WINDOW:
                raise ValueError(f"{where}: an average needs a window, a whole number of months from 1 to {MAX_WINDOW}")
            seg["window"] = w
        elif mode == "rate":
            if s.get("base") not in BASES:
                raise ValueError(f"{where}: base must be one of {', '.join(BASES)}")
            kind = s.get("rate_kind", "window")
            if kind not in RATE_KINDS:
                raise ValueError(f"{where}: rate_kind must be one of {', '.join(RATE_KINDS)}")
            seg.update(base=s["base"], rate_kind=kind)
            if kind in ("window", "ratio_avg") and s.get("window") is not None:
                w = s["window"]
                if isinstance(w, bool) or not isinstance(w, int) or not 1 <= w <= MAX_WINDOW:
                    raise ValueError(f"{where}: window must be a whole number from 1 to {MAX_WINDOW}")
                seg["window"] = w
            if kind == "typed":
                r = s.get("rate")
                if isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r) or not -5 <= r <= 5:
                    raise ValueError(f"{where}: typed needs a numeric rate (a fraction, 0.1 = 10%)")
                seg["rate"] = float(r)
        out.append(seg)
    froms = [s["from"] for s in out if s["from"]]
    if froms != sorted(froms) or len(set(froms)) != len(froms):
        raise ValueError("steps must run in order of their start months, one per month")
    return out


def segment_at(segments: list[dict], month: date) -> dict | None:
    """The last segment whose start is on or before `month` (null start = always)."""
    pick = None
    for s in segments:
        frm = s.get("from")
        if frm is None or date.fromisoformat(frm) <= month:
            pick = s
    return pick


# ---------------------------------------------------------------------------------------------------------------- COGS model

def cogs_model(rows: list[dict], settings: dict) -> dict:
    """Forward COGS % of revenue = blended landed cost per bottle / average price per bottle.
    blended cost = (1 - us_share) x China cost (SKU-mix weighted) + us_share x US cost (SKU-mix weighted)."""
    cfg = settings.get("cogs", {})
    mix_over = cfg.get("mix") or {}
    china = us = total_mix = 0.0
    lines = []
    every = []
    for r in rows:
        mix = float(mix_over.get(r["sku"], r.get("sales_mix") or 0.0))
        c = float((cfg.get("china_cost") or {}).get(r["sku"], r.get("china_unit_cost") or 0.0))
        u = float((cfg.get("us_cost") or {}).get(r["sku"], r.get("us_unit_cost") or 0.0))
        china += mix * c
        us += mix * u
        total_mix += mix
        every.append({"sku": r["sku"], "label": r.get("label"), "mix": mix, "china": c, "us": u})
        if mix:
            lines.append(every[-1])
    share = float(cfg["us_share"])
    price = float(cfg.get("price_per_bottle") or 0.0)
    cost = (1 - share) * china + share * us
    rate = cost / price if price else 0.0
    cur = float(cfg.get("current_price_per_bottle") or 0.0)
    return {"rate": rate, "cost_per_bottle": cost, "china_cost": china, "us_cost": us, "us_share": share, "price_per_bottle": price,
            "mix_total": total_mix, "current_price_per_bottle": cur, "rate_at_current_price": (cost / cur) if cur else None, "skus": lines, "all_skus": every}


# ---------------------------------------------------------------------------------------------------------------- revenue

def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def revenue_lines(drivers, cohorts, actuals: dict[str, Series]) -> dict[str, Series]:
    """nc_rev, repeat_rev, dtc_rev, amazon_rev, tiktok_rev, sales_tax per month. Months with loaded QuickBooks revenue use those; the rest come from
    the drivers module (new-customer revenue, Amazon, TikTok, sales tax) and the cohorts module (repeat revenue)."""
    repeat_c = dict(getattr(cohorts, "repeat_rev", None) or {})
    nc = getattr(drivers, "nc_rev", None) or {}
    dtc_act = actuals.get("dtc_rev", {})
    out: dict[str, Series] = {k: {} for k in ("nc_rev", "repeat_rev", "dtc_rev", "amazon_rev", "tiktok_rev", "sales_tax")}
    shared = None
    try:                                                      # one function shared with the Spend plan page when the drivers module provides it
        from ..drivers.revenue import revenue_lines as shared_fn
        shared = shared_fn(drivers, repeat_c, dtc_act) if drivers is not None else None
    except Exception:                                         # module not built yet or its signature moved: use the local rules below
        shared = None
    for m in MONTHS:
        n = float(nc.get(m, 0.0))
        out["nc_rev"][m] = n
        if shared is not None and m in getattr(shared, "dtc_rev", {}):
            rep = float(shared.repeat_rev.get(m, 0.0))
            dtc = float(shared.dtc_rev[m])
            tax = float(shared.sales_tax.get(m, 0.0))
        else:
            ov = (getattr(drivers, "repeat_override", None) or {}).get(m)
            rep = float(ov if ov is not None else repeat_c.get(m, 0.0))
            if m in dtc_act:
                dtc, tax = float(dtc_act[m]), 0.0
            else:
                dtc = rep + n
                st = (getattr(drivers, "sales_tax", None) or {}).get(m)
                tax = float(st) if st is not None else -float(getattr(drivers, "sales_tax_rate", 0.0) or 0.0) * dtc      # the rate is the drivers module's variable
        if m in dtc_act:
            dtc, tax = float(dtc_act[m]), 0.0
        out["repeat_rev"][m] = rep
        out["dtc_rev"][m] = dtc
        out["sales_tax"][m] = tax
        for k in ("amazon_rev", "tiktok_rev"):
            a = actuals.get(k, {})
            out[k][m] = float(a[m]) if m in a else float((getattr(drivers, k, None) or {}).get(m, 0.0))
    return out


def marketing_plan(drivers) -> tuple[Series, dict[str, Series]]:
    """(marketing_spend, channels) for projected months: row 51 semantics from the drivers module, with the channel split it carries."""
    ch_src = {"mkt_amazon_ads": "spend_amazon_ads", "mkt_facebook_ads": "spend_meta", "mkt_google_ads": "spend_google",
              "mkt_bing_ads": "spend_bing", "mkt_tiktok_ads": "spend_tiktok"}
    channels = {k: dict(getattr(drivers, src, None) or {}) for k, src in ch_src.items()}
    total: Series = {}
    explicit = getattr(drivers, "marketing_spend", None)
    spend_total = getattr(drivers, "spend_total", None) or {}
    for m in MONTHS:
        if explicit is not None and m in explicit:
            total[m] = float(explicit[m])
        else:
            s = sum(float(c.get(m, 0.0)) for c in channels.values())
            total[m] = max(s, float(spend_total.get(m, 0.0)))        # stand-in drivers carry row 11 (Meta + Google) in spend_total
    return total, channels


# ---------------------------------------------------------------------------------------------------------------- compute

def qb_through(actuals: dict[str, Series], today: date | None) -> date | None:
    cap = (today or date.today()).replace(day=1)
    months = [m for key, s in actuals.items() if key in L.QB_KEYS for m in s if m < cap]
    return max(months) if months else None


def _sum(vals: dict[str, Series], keys, m: date) -> float:
    return sum(vals[k].get(m, 0.0) for k in keys)


def compute_pnl(inputs: PnlInputs, drivers=None, cohorts=None, params: dict | None = None) -> PnlOutput:
    """The whole P&L for one scenario. `params` is the scenario's effective params['pnl']."""
    params = params or {}
    settings = effective_settings(inputs.settings, params)
    defaults = effective_drivers(inputs.drivers, params)
    eng = settings["engine"]
    actuals = inputs.actuals
    through = qb_through(actuals, inputs.today)
    out = PnlOutput(actual_through=through, settings=settings)
    is_act = {m: through is not None and m <= through for m in MONTHS}
    first_proj = next((m for m in MONTHS if not is_act[m]), None)
    n_window = int(eng.get("rate_window_months") or 4)
    cogs = cogs_model(inputs.cogs_sku, settings)
    out.cogs_model = cogs

    rev = revenue_lines(drivers, cohorts, actuals)
    mkt_total, mkt_channels = marketing_plan(drivers)
    v: dict[str, Series] = {k: {} for k in L.LINE_KEYS}                    # every line, month -> value (absent = blank)
    blank: dict[str, set] = {k: set() for k in L.LINE_KEYS}

    # ---- actual months: loaded values
    for m in MONTHS:
        if not is_act[m]:
            continue
        for k in L.QB_KEYS:
            if k in actuals and m in actuals[k]:
                v[k][m] = float(actuals[k][m])
            elif k not in L.REVENUE_KEYS:
                blank[k].add(m)
        for k in L.REVENUE_KEYS:
            v[k][m] = rev[k][m]
        v["nc_rev"][m] = rev["nc_rev"][m]
        v["repeat_rev"][m] = rev["repeat_rev"][m]
        v["sales_tax"][m] = 0.0
    for m in MONTHS:
        if is_act[m]:
            v["other_business_expenses"][m] = _sum(v, L.CHILDREN["other_business_expenses"], m)
            v["marketing_spend"][m] = _sum(v, L.CHANNEL_KEYS, m)
            v["total_revenue"][m] = v["dtc_rev"][m] + v["amazon_rev"][m] + v["tiktok_rev"][m] + v["sales_tax"][m]

    window = [m for m in MONTHS if is_act[m]][-n_window:] if through else []
    out.window = window

    def win(n: int | None) -> list[date]:
        k = n or n_window
        return [m for m in MONTHS if is_act[m]][-k:] if through else []

    # ---- resolved drivers
    pre: dict[str, Series] = {k: {} for k in L.DRIVER_KEYS}                # pre-inflation values (carry and avg read these)
    rate_cache: dict[tuple, tuple[float, list[date]]] = {}

    def line_series(key: str) -> Series:
        return v[key]

    def resolve_rate(key: str, seg: dict) -> tuple[float, list[date]]:
        kind = seg.get("rate_kind", "window")
        base = seg["base"]
        if kind == "typed":
            return float(seg.get("rate", 0.0)), []
        if kind == "cogs_model":
            return cogs["rate"], []
        ck = (key, base, kind, seg.get("window"))
        if ck in rate_cache:
            return rate_cache[ck]
        months = win(seg.get("window"))
        if kind == "window":
            num = sum(v[key].get(m, 0.0) for m in months)
            den = sum(v[base].get(m, 0.0) for m in months)
            r = num / den if den else 0.0
        else:                                                             # ratio_avg
            ratios = [v[key].get(m, 0.0) / v[base][m] for m in months if v[base].get(m)]
            r = sum(ratios) / len(ratios) if ratios else 0.0
        rate_cache[ck] = (r, months)
        return rate_cache[ck]

    def infl(key: str, m: date) -> float:
        if through is None:
            return 1.0
        k = (m.year - through.year) * 12 + m.month - through.month
        rate = float(eng.get("cogs_inflation" if key in COGS_GROUP else "opex_inflation") or 0.0)
        return (1.0 + rate) ** (k / 12.0) if k > 0 and rate else 1.0

    hc = settings["headcount"]

    def headcount_value(m: date, total_rev: float) -> float:
        start = date.fromisoformat(hc["start"]) if hc.get("start") else None
        if start and m < start:
            return 0.0
        steps = math.floor(max(0.0, total_rev * 12 - float(hc["threshold"])) / float(hc["step"]))
        return float(hc["base"]) + steps * float(hc["increment"])

    # ---- projected months, in order (carry and avg read earlier months; rates read the actual window)
    for m in MONTHS:
        if is_act[m]:
            continue
        v["nc_rev"][m] = rev["nc_rev"][m]
        v["repeat_rev"][m] = rev["repeat_rev"][m]
        v["dtc_rev"][m] = rev["dtc_rev"][m]
        v["amazon_rev"][m] = rev["amazon_rev"][m]
        v["tiktok_rev"][m] = rev["tiktok_rev"][m]
        v["sales_tax"][m] = rev["sales_tax"][m]
        v["total_revenue"][m] = v["dtc_rev"][m] + v["amazon_rev"][m] + v["tiktok_rev"][m] + v["sales_tax"][m]
        # marketing spend and its channel split come from the drivers module (a scenario override of a channel changes only that line)
        for k in L.CHANNEL_KEYS:
            ov = inputs.overrides.get(k, {}).get(m)
            v[k][m] = float(ov) if ov is not None else float(mkt_channels[k].get(m, 0.0))
        ov = inputs.overrides.get("marketing_spend", {}).get(m)
        v["marketing_spend"][m] = float(ov) if ov is not None else mkt_total[m]
        for k in L.DRIVER_KEYS + L.DRIVEN_PARENTS:
            ov = inputs.overrides.get(k, {}).get(m)
            if ov is not None:
                v[k][m] = float(ov)
                pre.setdefault(k, {})[m] = float(ov)
                continue
            segs = defaults.get(k)
            seg = segment_at(segs, m) if segs else None
            if seg is None:
                val = 0.0
            else:
                mode = seg["mode"]
                if mode == "fixed":
                    val = float(seg.get("value", 0.0)) * infl(k, m)
                    pre.setdefault(k, {})[m] = float(seg.get("value", 0.0))
                elif mode == "carry":
                    prev = add_months(m, -1)
                    base_val = pre.get(k, {}).get(prev, v[k].get(prev, 0.0))
                    pre.setdefault(k, {})[m] = base_val
                    val = base_val * infl(k, m)
                elif mode == "avg":
                    n = int(seg["window"])
                    hist = [pre.get(k, {}).get(add_months(m, -i), v[k].get(add_months(m, -i), 0.0)) for i in range(1, n + 1)]
                    base_val = sum(hist) / n
                    pre.setdefault(k, {})[m] = base_val
                    val = base_val * infl(k, m)
                elif mode == "rate":
                    r, _ = resolve_rate(k, seg)
                    val = r * v[seg["base"]][m] * infl(k, m)
                    pre.setdefault(k, {})[m] = r * v[seg["base"]][m]
                elif mode == "headcount":
                    val = headcount_value(m, v["total_revenue"][m])
                else:
                    val = 0.0
            v[k][m] = val
        # children of other_business_expenses are blank in projected months (the parent carries the whole amount)
        for k in L.CHILDREN["other_business_expenses"]:
            blank[k].add(m)

    # ---- sums, totals, margins
    for m in MONTHS:
        v["amazon_fees"][m] = _sum(v, L.CHILDREN["amazon_fees"], m)
        v["cogs"][m] = _sum(v, L.CHILDREN["cogs"], m)
        v["payment_processing"][m] = _sum(v, L.CHILDREN["payment_processing"], m)
        v["total_cogs"][m] = (v["payment_processing"][m] + v["inbound_shipping"].get(m, 0.0) + v["fulfillment_services"].get(m, 0.0)
                              + v["cogs"][m] + v["amazon_fees"][m] + v["inventory_shrinkage"].get(m, 0.0))
        v["gross_profit"][m] = v["total_revenue"][m] - v["total_cogs"][m]
        v["marketing"][m] = _sum(v, L.CHILDREN["marketing"], m)
        v["overhead"][m] = _sum(v, L.CHILDREN["overhead"], m)
        v["total_expenses"][m] = (v["marketing_spend"][m] + v["marketing"][m] + v["other_business_expenses"][m] + v["headcount"].get(m, 0.0)
                                  + v["overhead"][m] + v["uncategorized"].get(m, 0.0))
        v["non_media_expenses"][m] = v["total_expenses"][m] - v["marketing_spend"][m]
        v["noi"][m] = v["gross_profit"][m] - v["total_expenses"][m]
        for rk, (num, den) in L.RATIOS.items():
            d = v[den].get(m, 0.0)
            if d:
                v[rk][m] = v[num].get(m, 0.0) / d
    trailing_metrics(v, int(settings["trend"]["avg_window"]))

    # ---- result
    for k, s in v.items():
        for m in blank.get(k, ()):
            s.pop(m, None)
        out.lines[k] = s
    out.fy = fiscal_years(v)
    out.fy_flat = {f"{y}.{k}": val for y, row in out.fy.items() for k, val in row.items()}
    out.ttm = dict(v["ttm_noi"])
    out.ftm = dict(v["ftm_noi"])
    out.ev = implied_ev(out.fy, settings["ev"])
    out.driver_info = describe_drivers(defaults, v, resolve_rate, first_proj, n_window, win, cogs)
    if not through:
        out.warnings.append("No QuickBooks history is loaded: every month is projected from the drivers.")
    if not defaults:
        out.warnings.append("No cost drivers are loaded: projected cost lines are zero until the driver defaults are seeded.")
    absent = [g for g in SCENARIO_GROUPS if g not in inputs.settings]
    if absent:
        out.warnings.append(f"Settings not loaded for {', '.join(absent)}: the built-in starting values are in use until the settings are seeded.")
    return out


# ---------------------------------------------------------------------------------------------------------------- derived metrics

def trailing_metrics(v: dict[str, Series], avg_window: int) -> None:
    """TTM, FTM and growth rows, in place. Twelve months is a year (trailing sums, run-rate and year-over-year lag); `avg_window` is the growth-average setting."""
    tot, noi, dtc = v["total_revenue"], v["noi"], v["dtc_rev"]
    for i, m in enumerate(MONTHS):
        v["ftm_revenue"][m] = tot.get(m, 0.0) * 12
        v["ftm_noi"][m] = noi.get(m, 0.0) * 12
        if i >= 11:
            win = MONTHS[i - 11: i + 1]
            tr, tn = sum(tot.get(x, 0.0) for x in win), sum(noi.get(x, 0.0) for x in win)
            v["ttm_revenue"][m], v["ttm_noi"][m] = tr, tn
            if tr:
                v["ttm_noi_margin"][m] = tn / tr

    def mom(src: Series, dst: Series, positive_only: bool) -> None:
        for i in range(1, len(MONTHS)):
            prev, cur = src.get(MONTHS[i - 1], 0.0), src.get(MONTHS[i], 0.0)
            if (prev <= 0) if positive_only else (prev == 0):
                continue
            dst[MONTHS[i]] = cur / prev - 1

    def yoy(src: Series, dst: Series, positive_only: bool) -> None:
        for i in range(12, len(MONTHS)):
            prev, cur = src.get(MONTHS[i - 12], 0.0), src.get(MONTHS[i], 0.0)
            if (prev <= 0) if positive_only else (prev == 0):
                continue
            dst[MONTHS[i]] = cur / prev - 1

    def avg6(src: Series, dst: Series) -> None:
        n = max(1, avg_window)
        for i in range(n - 1, len(MONTHS)):
            vals = [src.get(x) for x in MONTHS[i - n + 1: i + 1]]
            if all(x is not None for x in vals):
                dst[MONTHS[i]] = sum(vals) / n

    for base, pos in (("dtc_rev", False), ("total_rev", False), ("noi", True)):
        src = {"dtc_rev": dtc, "total_rev": tot, "noi": noi}[base]
        mom(src, v[f"{base}_mom"], pos)
        yoy(src, v[f"{base}_yoy"], pos)
        avg6(v[f"{base}_mom"], v[f"{base}_mom_6m"])
        avg6(v[f"{base}_yoy"], v[f"{base}_yoy_6m"])


def fiscal_years(v: dict[str, Series]) -> dict[int, dict[str, float]]:
    """Calendar-year roll-up of every line: sums for money lines, ratios recomputed from the sums."""
    out: dict[int, dict[str, float]] = {}
    for y in sorted({m.year for m in MONTHS}):
        months = [m for m in MONTHS if m.year == y]
        row = {k: sum(v[k].get(m, 0.0) for m in months) for k in L.LINE_KEYS if k not in L.NON_SUMMED}
        for rk, (num, den) in L.RATIOS.items():
            if row.get(den):
                row[rk] = row.get(num, 0.0) / row[den]
        row["ttm_noi"] = v["ttm_noi"].get(months[-1], 0.0)
        row["ttm_revenue"] = v["ttm_revenue"].get(months[-1], 0.0)
        out[y] = row
    return out


def implied_ev(fy: dict[int, dict[str, float]], cfg: dict) -> dict:
    """Implied enterprise value = full-year net operating income x multiple ("N/A" -> None when the year is negative).
    Flat keys '3x'..'8x' are read by the UI shell headline; the rest is descriptive."""
    year = int(cfg["year"])
    noi = (fy.get(year) or {}).get("noi")
    out: dict = {"basis_year": year, "basis_noi": noi, "loi": float(cfg["loi"])}
    mults = []
    for mlt in cfg["multiples"]:
        mults.append(mlt)
        key = f"{mlt:g}x"
        out[key] = None if noi is None or noi < 0 else noi * mlt
    out["multiples"] = mults
    return out


def describe_drivers(defaults, v, resolve_rate, first_proj, n_window, win, cogs) -> dict[str, dict]:
    """One entry per driven line for the driver table: the step in force at the first projected month and what it resolves to."""
    info: dict[str, dict] = {}
    for k in L.DRIVER_KEYS + L.DRIVEN_PARENTS:
        segs = defaults.get(k) or []
        seg = segs[-1] if segs else None                                  # the long-run step: what the line does once every step has started
        e: dict = {"segments": segs, "mode": seg["mode"] if seg else None}
        if seg:
            if seg["mode"] == "fixed":
                e["value"] = seg.get("value", 0.0)
            elif seg["mode"] == "rate":
                r, months = resolve_rate(k, seg)
                e.update(base=seg["base"], rate_kind=seg.get("rate_kind", "window"), rate=r, window=[x.isoformat() for x in months])
            elif seg["mode"] == "avg":
                e["window_months"] = seg["window"]
        info[k] = e
    return info
