"""The qb2026 chart: the numbered QuickBooks chart of accounts Josh now keeps his books on (4xxx income, 5xxx cost of goods sold, 6xxx expenses,
7xxx/8xxx other).

How a month is computed
    books      a closed month holding any loaded `qb26.*` actual: every leaf is its loaded value (blank stays blank), parents are sums.
    old        a month before the first books month, a gap month between books months, or every month when no new-format books are loaded: the leaves are
               the sheet chart's computed output for the same scenario (its books through Jul 2026, then its feeds and projected months) rolled up through
               CROSSWALK. Three roll-up lines (legacy_*) hold the old-format amounts that have no account here, so old history ties to the dollar.
    feeds / projected   months after the last books month. Revenue is exactly what the sheet chart gets (the drivers and cohorts modules); no sales tax
               is deducted (the books have no such line). Every other leaf follows a driver whose rates and fixed amounts are re-derived from the trailing
               loaded books months (`engine.rate_window_months`, books months only, so loading another month moves every rate with no other change).

Driver modes: fixed {value}, carry, avg {window} (same as the sheet chart), rate {base, rate_kind window|ratio_avg|typed, window?, rate?} and
books_mean {window?}: the mean of the line over the trailing books months (the sheet chart's `avg` also averages projected months).

`legacy_lines` is the view other modules read: the old line keys (total_revenue, dtc_rev, china_cogs, seller_fees ...) built from the new lines, plus every
new key. Interest expense and other income are NOT pushed into the legacy overhead lines: `noi` is net operating income before them.
"""
from __future__ import annotations

import copy
import math
from datetime import date
from typing import Any

from ...interfaces import AXIS_END, AXIS_START, MONTHS, Series, add_months
from .. import lines as L
from ..lines import Line
from .. import model as M
from . import ChartDef, QB2026


def _q(key: str, label: str, kind: str, parent: str | None = None, indent: int = 0, fmt: str = "usd0", note: str | None = None) -> Line:
    return Line(key, label, None, kind, fmt, parent, indent, note)


_OLD_FORMAT = "Old-format lines with no account in the new books"
_SHEET_TRAIL = ("ftm_revenue", "ftm_noi", "ttm_revenue", "ttm_noi", "ttm_noi_margin", "dtc_rev_mom", "dtc_rev_mom_6m", "dtc_rev_yoy", "dtc_rev_yoy_6m",
                "total_rev_mom", "total_rev_mom_6m", "total_rev_yoy", "total_rev_yoy_6m", "noi_mom", "noi_mom_6m", "noi_yoy", "noi_yoy_6m")

_LINES: list[Line] = [
    # income
    _q("nc_rev", "New-customer revenue", "upstream"),
    _q("repeat_rev", "Repeat revenue", "upstream"),
    _q("dtc_sales", "4020 DTC sales", "qb", "gross_sales", 1),
    _q("amazon_sales", "4010 Amazon sales", "qb", "gross_sales", 1),
    _q("tiktok_sales", "4030 TikTok sales", "qb", "gross_sales", 1),
    _q("gross_sales", "Total for 4000 Sales", "sum"),
    _q("shipping_income", "4200 Shipping income", "qb"),
    _q("refunds_discounts", "4400 Refunds and discounts to customers", "qb"),
    _q("legacy_income_adj", "Old-format income adjustments (no account in the new books)", "legacy", note=_OLD_FORMAT),
    _q("total_income", "Total income (net revenue)", "calc"),
    _q("refunds_pct", "Refunds and discounts % of gross sales", "ratio", fmt="pct"),
    # cost of goods sold
    _q("cogs_amazon", "5010 Amazon cost of goods", "qb", "cogs_product", 1),
    _q("cogs_shopify", "5020 Shopify cost of goods", "qb", "cogs_product", 1),
    _q("cogs_tiktok", "5030 TikTok cost of goods", "qb", "cogs_product", 1),
    _q("cogs_product", "Total for 5000 Cost of goods sold", "sum"),
    _q("threepl_services", "5310 Third-party logistics (3PL) services", "qb", "fulfillment", 1),
    _q("fba_fees", "5320 Fulfillment by Amazon (FBA) fees", "qb", "fulfillment", 1),
    _q("fulfillment", "Total for 5300 Fulfillment", "sum"),
    _q("adyen_fees", "5410 Adyen fees", "qb", "seller_fees_total", 1),
    _q("amazon_fees", "5420 Amazon fees", "qb", "seller_fees_total", 1),
    _q("paypal_fees", "5430 PayPal fees", "qb", "seller_fees_total", 1),
    _q("shopify_fees", "5440 Shopify fees", "qb", "seller_fees_total", 1),
    _q("tiktok_fees", "5450 TikTok fees", "qb", "seller_fees_total", 1),
    _q("seller_fees_total", "Total for 5400 Seller fees", "sum"),
    _q("legacy_cogs_other", "Old-format cost of goods (no account in the new books)", "legacy", note=_OLD_FORMAT),
    _q("total_cogs", "Total cost of goods sold", "calc"),
    _q("gross_profit", "Gross profit", "calc"),
    _q("gross_margin", "Gross margin % of total income", "ratio", fmt="pct"),
    # marketing
    _q("creative_agency", "6011 Creative agency", "qb", "marketing_agency_total", 1),
    _q("email_agency", "6012 Email agency", "qb", "marketing_agency_total", 1),
    _q("marketing_agency_total", "Total for 6010 Marketing agency", "sum", "marketing_total", 1),
    _q("amazon_ads", "6031 Amazon ads", "qb", "marketing_media_total", 1),
    _q("facebook_ads", "6032 Facebook ads", "qb", "marketing_media_total", 1),
    _q("google_ads", "6033 Google ads", "qb", "marketing_media_total", 1),
    _q("tiktok_ads", "6035 TikTok ads", "qb", "marketing_media_total", 1),
    _q("marketing_media_total", "Total for 6030 Marketing media", "sum", "marketing_total", 1),
    _q("marketing_total", "Total for 6000 Marketing", "sum"),
    # overhead
    _q("accounting", "6110 Accounting", "qb", "overhead_total", 1),
    _q("chargeback_management", "6115 Chargeback management", "qb", "overhead_total", 1),
    _q("consulting", "6120 Consulting", "qb", "overhead_total", 1),
    _q("customer_service", "6125 Customer service", "qb", "overhead_total", 1),
    _q("legal", "6135 Legal", "qb", "overhead_total", 1),
    _q("office_supplies", "6145 Office supplies", "qb", "overhead_total", 1),
    _q("software_marketing", "6166 Software and apps, marketing", "qb", "software_total", 2),
    _q("software_operations", "6167 Software and apps, operations", "qb", "software_total", 2),
    _q("software_total", "Total for 6165 Software and apps", "sum", "overhead_total", 1),
    _q("bank_fees", "6170 Bank fees", "qb", "overhead_total", 1),
    _q("overhead_total", "Total for 6100 Overhead expenses", "sum"),
    _q("legacy_expenses_other", "Old-format expenses (no account in the new books)", "legacy", note=_OLD_FORMAT),
    _q("total_expenses", "Total expenses", "calc"),
    _q("noi", "Net operating income", "calc"),
    _q("noi_margin", "Net operating income margin", "ratio", fmt="pct"),
    # below the line
    _q("other_income", "7000 Other income", "qb"),
    _q("interest_expense", "8000 Interest expense", "qb"),
    _q("net_other_income", "Net other income", "calc"),
    _q("net_income", "Net income", "calc"),
    _q("net_income_margin", "Net income margin", "ratio", fmt="pct"),
]
_LINES += [Line(k, L.LINE_KEYS[k].label, None, "trail", L.LINE_KEYS[k].fmt) for k in _SHEET_TRAIL if k not in {x.key for x in _LINES}]
LINE_KEYS: dict[str, Line] = {ln.key: ln for ln in _LINES}
assert len(LINE_KEYS) == len(_LINES), "duplicate qb2026 line key"

ACCOUNTS: dict[str, str] = {"amazon_sales": "4010", "dtc_sales": "4020", "tiktok_sales": "4030", "shipping_income": "4200", "refunds_discounts": "4400",
                            "cogs_amazon": "5010", "cogs_shopify": "5020", "cogs_tiktok": "5030", "threepl_services": "5310", "fba_fees": "5320",
                            "adyen_fees": "5410", "amazon_fees": "5420", "paypal_fees": "5430", "shopify_fees": "5440", "tiktok_fees": "5450",
                            "creative_agency": "6011", "email_agency": "6012", "amazon_ads": "6031", "facebook_ads": "6032", "google_ads": "6033",
                            "tiktok_ads": "6035", "accounting": "6110", "chargeback_management": "6115", "consulting": "6120", "customer_service": "6125",
                            "legal": "6135", "office_supplies": "6145", "software_marketing": "6166", "software_operations": "6167", "bank_fees": "6170",
                            "other_income": "7000", "interest_expense": "8000"}
QB_KEYS: tuple[str, ...] = tuple(ln.key for ln in _LINES if ln.kind == "qb")
assert set(QB_KEYS) == set(ACCOUNTS), "every loadable line has an account"
CHILDREN: dict[str, tuple[str, ...]] = {}
for _ln in _LINES:
    if _ln.parent:
        CHILDREN[_ln.parent] = CHILDREN.get(_ln.parent, ()) + (_ln.key,)

CHANNEL_KEYS = ("facebook_ads", "google_ads")                     # typed spend from the drivers module (the spend plan)
INCOME_LEAVES = ("dtc_sales", "amazon_sales", "tiktok_sales", "shipping_income", "refunds_discounts")
SALES_KEYS = ("dtc_sales", "amazon_sales", "tiktok_sales")
DRIVER_KEYS: tuple[str, ...] = tuple(k for k in QB_KEYS if k not in SALES_KEYS and k not in CHANNEL_KEYS)
BELOW_NOI = ("interest_expense", "other_income")                 # net operating income is before these (the sheet chart's is after interest)
NO_INFLATION = frozenset(("shipping_income", "refunds_discounts", "other_income"))          # revenue-side lines are not inflated like costs
COGS_GROUP = ("cogs_amazon", "cogs_shopify", "cogs_tiktok")
BASES = ("dtc_sales", "amazon_sales", "tiktok_sales", "gross_sales")
MODES = ("fixed", "carry", "avg", "rate", "books_mean")
RATE_KINDS = ("window", "ratio_avg", "typed")

# ---------------------------------------------------------------------------------------------------------------- old chart -> new chart
CROSSWALK: tuple[tuple[str, str], ...] = (
    ("dtc_rev", "dtc_sales"), ("amazon_rev", "amazon_sales"), ("tiktok_rev", "tiktok_sales"), ("sales_tax", "legacy_income_adj"),
    ("china_cogs", "cogs_shopify"), ("us_cogs", "cogs_shopify"),
    ("amazon_fba_fees", "fba_fees"), ("amazon_seller_fees", "amazon_fees"), ("fulfillment_services", "threepl_services"),
    ("seller_fees", "adyen_fees"), ("shopify_subscriptions", "shopify_fees"),
    ("inventory_shrinkage", "legacy_cogs_other"), ("inbound_shipping", "legacy_cogs_other"), ("shipping_supplies", "legacy_cogs_other"),
    ("amazon_subscriptions", "legacy_cogs_other"), ("amazon_inbound_transport", "legacy_cogs_other"),
    ("mkt_amazon_ads", "amazon_ads"), ("mkt_facebook_ads", "facebook_ads"), ("mkt_google_ads", "google_ads"), ("mkt_tiktok_ads", "tiktok_ads"),
    ("marketing_agency", "creative_agency"), ("marketing_software", "software_marketing"), ("software_apps", "software_operations"),
    ("office_admin", "office_supplies"), ("accounting", "accounting"), ("chargeback_management", "chargeback_management"), ("consulting", "consulting"),
    ("customer_service", "customer_service"), ("legal", "legal"), ("bank_fees", "bank_fees"), ("interest_expense", "interest_expense"),
)
# Old lines that roll into legacy_expenses_other. other_business_expenses is the parent: in books months it already contains bank_fees (which has its own
# account here), so bank_fees is taken out of it; in projected sheet months the parent holds the whole amount and bank_fees is blank.
LEGACY_EXPENSE_OLD = ("mkt_bing_ads", "marketing_services", "marketing_influencers", "headcount", "insurance", "memberships", "outside_services",
                      "sales_tax_filing", "samples", "uncategorized")


def crosswalk_month(old: dict[str, Series], m: date) -> dict[str, float]:
    """The new-chart leaves and legacy roll-ups of one old-format month, from the sheet chart's lines. A leaf with no contributing old value stays blank."""
    out: dict[str, float] = {}
    for okey, nkey in CROSSWALK:
        s = old.get(okey) or {}
        if m in s:
            out[nkey] = out.get(nkey, 0.0) + float(s[m])
    g = lambda k: float((old.get(k) or {}).get(m, 0.0))                   # noqa: E731
    out["legacy_income_adj"] = out.get("legacy_income_adj", 0.0)
    out["legacy_cogs_other"] = out.get("legacy_cogs_other", 0.0)
    out["legacy_expenses_other"] = sum(g(k) for k in LEGACY_EXPENSE_OLD) + g("other_business_expenses") - g("bank_fees")
    return out


# ---------------------------------------------------------------------------------------------------------------- the chart definition
SECTIONS: list[tuple[str, list[str]]] = [
    ("Income", ["nc_rev", "repeat_rev", "dtc_sales", "amazon_sales", "tiktok_sales", "gross_sales", "shipping_income", "refunds_discounts",
                "legacy_income_adj", "total_income", "refunds_pct"]),
    ("Cost of goods sold", ["cogs_amazon", "cogs_shopify", "cogs_tiktok", "cogs_product", "threepl_services", "fba_fees", "fulfillment",
                            "adyen_fees", "amazon_fees", "paypal_fees", "shopify_fees", "tiktok_fees", "seller_fees_total", "legacy_cogs_other",
                            "total_cogs"]),
    ("Gross profit", ["gross_profit", "gross_margin"]),
    ("Marketing", ["creative_agency", "email_agency", "marketing_agency_total", "amazon_ads", "facebook_ads", "google_ads", "tiktok_ads",
                   "marketing_media_total", "marketing_total"]),
    ("Overhead", ["accounting", "chargeback_management", "consulting", "customer_service", "legal", "office_supplies", "software_marketing",
                  "software_operations", "software_total", "bank_fees", "overhead_total", "legacy_expenses_other"]),
    ("Net operating income", ["total_expenses", "noi", "noi_margin"]),
    ("Below the line", ["other_income", "interest_expense", "net_other_income", "net_income", "net_income_margin"]),
]
DRIVER_GROUPS: list[tuple[str, list[str]]] = [
    ("Income", ["shipping_income", "refunds_discounts"]),
    ("Cost of goods sold", ["cogs_amazon", "cogs_shopify", "cogs_tiktok", "threepl_services", "fba_fees", "adyen_fees", "amazon_fees", "paypal_fees",
                            "shopify_fees", "tiktok_fees"]),
    ("Marketing", ["creative_agency", "email_agency", "amazon_ads", "tiktok_ads"]),
    ("Overhead", ["accounting", "chargeback_management", "consulting", "customer_service", "legal", "office_supplies", "software_marketing",
                  "software_operations", "bank_fees"]),
    ("Below the line", ["other_income", "interest_expense"]),
]
RATIOS: dict[str, tuple[str, str]] = {"refunds_pct": ("refunds_discounts", "gross_sales"), "gross_margin": ("gross_profit", "total_income"),
                                      "noi_margin": ("noi", "total_income"), "net_income_margin": ("net_income", "total_income")}
FAMILIES: dict[str, tuple[str, ...]] = {
    "revenue": SALES_KEYS,
    "cost_of_goods": ("cogs_amazon", "cogs_shopify", "cogs_tiktok"),
    "fulfillment": ("threepl_services", "fba_fees"),
    "marketing_spend": ("amazon_ads", "facebook_ads", "google_ads", "tiktok_ads"),
    "operating_expenses": ("creative_agency", "email_agency", "accounting", "chargeback_management", "consulting", "customer_service", "legal",
                           "office_supplies", "software_marketing", "software_operations", "bank_fees"),
}
FAMILY_LABEL = {"revenue": "revenue", "cost_of_goods": "cost of goods", "fulfillment": "fulfillment", "marketing_spend": "ad spend",
                "operating_expenses": "operating expenses"}
NON_SUMMED = frozenset(k for k, ln in LINE_KEYS.items() if ln.kind in ("ratio", "trail"))

CHART = ChartDef(
    name=QB2026, label="New chart (numbered QuickBooks accounts)", line_keys=LINE_KEYS, qb_keys=QB_KEYS, actuals_prefix="qb26.", series_prefix="pnl.qb26.",
    driver_keys=DRIVER_KEYS, driven_parents=(), channel_keys=CHANNEL_KEYS, bases=BASES, modes=MODES, rate_kinds=RATE_KINDS, sections=SECTIONS,
    driver_groups=DRIVER_GROUPS, income_keys=INCOME_LEAVES, families=FAMILIES, family_label=FAMILY_LABEL, params_ns=("qb26", "drivers"),
    total_keys=("total_income", "gross_profit", "noi", "net_income"), subtotal_calc=("total_cogs", "total_expenses", "net_other_income"),
    trend_keys=("ttm_revenue", "ttm_noi", "ttm_noi_margin", "ftm_revenue", "ftm_noi", "dtc_rev_mom", "dtc_rev_mom_6m", "dtc_rev_yoy", "dtc_rev_yoy_6m",
                "total_rev_mom", "total_rev_mom_6m", "total_rev_yoy", "total_rev_yoy_6m", "noi_mom", "noi_mom_6m", "noi_yoy", "noi_yoy_6m"),
    accounts=ACCOUNTS)


# ---------------------------------------------------------------------------------------------------------------- drivers
def validate_segments(key: str, segments: Any) -> list[dict]:
    """Normalise and check one qb2026 driver. ValueError naming the problem."""
    if key not in DRIVER_KEYS:
        raise ValueError(f"{key!r} is not a driven line of the new chart")
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
        elif mode in ("avg", "books_mean"):
            w = s.get("window")
            if mode == "books_mean" and w is None:
                pass
            elif isinstance(w, bool) or not isinstance(w, int) or not 1 <= w <= M.MAX_WINDOW:
                raise ValueError(f"{where}: an average needs a window, a whole number of months from 1 to {M.MAX_WINDOW}")
            else:
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
                if isinstance(w, bool) or not isinstance(w, int) or not 1 <= w <= M.MAX_WINDOW:
                    raise ValueError(f"{where}: window must be a whole number from 1 to {M.MAX_WINDOW}")
                seg["window"] = w
            if kind == "typed":
                r = s.get("rate")
                if isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r) or not -1 <= r <= 1:
                    raise ValueError(f"{where}: typed needs a rate between -1 and 1 (a fraction, 0.1 = 10%)")
                seg["rate"] = float(r)
        out.append(seg)
    froms = [s["from"] for s in out if s["from"]]
    if froms != sorted(froms) or len(set(froms)) != len(froms):
        raise ValueError("steps must run in order of their start months, one per month")
    return out


# ---------------------------------------------------------------------------------------------------------------- compute
def books_months(qb_actuals: dict[str, Series], today: date) -> list[date]:
    """Closed months (inside the model's axis) holding any loaded qb26 value, oldest first."""
    cap = today.replace(day=1)
    return sorted({m for k, s in qb_actuals.items() if k in QB_KEYS for m in s if m < cap and AXIS_START <= m <= AXIS_END})


def _sum(v: dict[str, Series], keys, m: date) -> float:
    return sum(v[k].get(m, 0.0) for k in keys)


def compute(inputs: M.PnlInputs, drivers, cohorts, params: dict, old: M.PnlOutput) -> M.PnlOutput:
    """The qb2026 P&L for one scenario, built on `old` (the sheet chart's output for the same scenario)."""
    settings = old.settings
    eng = settings["engine"]
    qa = inputs.qb_actuals or {}
    today = inputs.today
    if today is None:
        from ..store import today as clock_today
        today = clock_today()
    books = books_months(qa, today)
    books_set = set(books)
    through = books[-1] if books else None
    first_books = books[0] if books else None
    feeds_through = old.feeds_through
    n_window = int(eng.get("rate_window_months") or 4)
    defaults = M.effective_drivers(inputs.qb_drivers or {}, {"drivers": ((params.get("qb26") or {}).get("drivers")) or {}})
    status: dict[date, str] = {}
    for m in MONTHS:
        if m in books_set:
            status[m] = "books"
        elif through is not None and m > through:
            status[m] = "feeds" if feeds_through is not None and m <= feeds_through else "projected"
        else:
            status[m] = "old"
    out = copy.copy(old)
    out.chart = QB2026
    out.below_noi = BELOW_NOI
    out.month_status = status
    out.books_through = through if through is not None else old.books_through
    out.actual_through = out.books_through
    out.feeds_through = feeds_through
    out.sheet_books_through = old.books_through
    out.old_format_through = max([m for m in MONTHS if status[m] == "old"], default=None)
    out.warnings = []
    if not books:
        out.warnings.append("No new-format books are loaded: every month shows the earlier chart rolled up to the new lines until a month is uploaded.")
    if not defaults:
        out.warnings.append("No new-chart drivers are loaded: projected cost lines are zero until the driver defaults are seeded.")

    old_lines = old.lines
    v: dict[str, Series] = {k: {} for k in LINE_KEYS}

    # ---- every month: the upstream revenue rows
    for m in MONTHS:
        for k in ("nc_rev", "repeat_rev"):
            if m in (old_lines.get(k) or {}):
                v[k][m] = old_lines[k][m]

    # ---- books months and old-format months
    for m in MONTHS:
        if status[m] == "books":
            for k in QB_KEYS:
                if m in qa.get(k, {}):
                    v[k][m] = float(qa[k][m])
        elif status[m] == "old":
            for k, val in crosswalk_month(old_lines, m).items():
                v[k][m] = val

    def win(n: int | None) -> list[date]:
        return books[-(n or n_window):] if books else []

    out.window = win(None)
    rate_cache: dict[tuple, tuple[float, list[date]]] = {}

    def resolve_rate(key: str, seg: dict) -> tuple[float, list[date]]:
        kind = seg.get("rate_kind", "window")
        if kind == "typed":
            return float(seg.get("rate", 0.0)), []
        ck = (key, seg["base"], kind, seg.get("window"))
        if ck in rate_cache:
            return rate_cache[ck]
        months = win(seg.get("window"))
        if kind == "window":
            num = sum(v[key].get(m, 0.0) for m in months)
            den = sum(_base(seg["base"], m) for m in months)
            r = num / den if den else 0.0
        else:
            ratios = [v[key].get(m, 0.0) / _base(seg["base"], m) for m in months if _base(seg["base"], m)]
            r = sum(ratios) / len(ratios) if ratios else 0.0
        rate_cache[ck] = (r, months)
        return rate_cache[ck]

    def _base(name: str, m: date) -> float:
        if name == "gross_sales":
            return _sum(v, SALES_KEYS, m)
        return v[name].get(m, 0.0)

    def books_mean(key: str, seg: dict) -> tuple[float, list[date]]:
        months = win(seg.get("window"))
        return (sum(v[key].get(m, 0.0) for m in months) / len(months) if months else 0.0), months

    def infl(key: str, m: date) -> float:
        if through is None or key in NO_INFLATION:
            return 1.0
        k = (m.year - through.year) * 12 + m.month - through.month
        rate = float(eng.get("cogs_inflation" if key in COGS_GROUP else "opex_inflation") or 0.0)
        return (1.0 + rate) ** (k / 12.0) if k > 0 and rate else 1.0

    # ---- projected months, in order
    _, mkt_channels = M.marketing_plan(drivers)
    pre: dict[str, Series] = {k: {} for k in DRIVER_KEYS}
    over = inputs.qb_overrides or {}
    for m in MONTHS:
        if status[m] in ("books", "old"):
            continue
        for k, src in (("dtc_sales", "dtc_rev"), ("amazon_sales", "amazon_rev"), ("tiktok_sales", "tiktok_rev")):
            v[k][m] = float((old_lines.get(src) or {}).get(m, 0.0))
        for k, okey in (("facebook_ads", "mkt_facebook_ads"), ("google_ads", "mkt_google_ads")):
            ov = over.get(k, {}).get(m)
            v[k][m] = float(ov) if ov is not None else float(mkt_channels[okey].get(m, 0.0))
        for k in DRIVER_KEYS:
            ov = over.get(k, {}).get(m)
            if ov is not None:
                v[k][m] = float(ov)
                pre[k][m] = float(ov)
                continue
            segs = defaults.get(k)
            seg = M.segment_at(segs, m) if segs else None
            if seg is None:
                val = 0.0
            else:
                mode = seg["mode"]
                if mode == "fixed":
                    val = float(seg.get("value", 0.0)) * infl(k, m)
                    pre[k][m] = float(seg.get("value", 0.0))
                elif mode == "carry":
                    base_val = pre[k].get(add_months(m, -1), v[k].get(add_months(m, -1), 0.0))
                    pre[k][m] = base_val
                    val = base_val * infl(k, m)
                elif mode == "avg":
                    n = int(seg["window"])
                    hist = [pre[k].get(add_months(m, -i), v[k].get(add_months(m, -i), 0.0)) for i in range(1, n + 1)]
                    base_val = sum(hist) / n
                    pre[k][m] = base_val
                    val = base_val * infl(k, m)
                elif mode == "books_mean":
                    base_val, _ = books_mean(k, seg)
                    pre[k][m] = base_val
                    val = base_val * infl(k, m)
                elif mode == "rate":
                    r, _ = resolve_rate(k, seg)
                    base_amt = _base(seg["base"], m)
                    pre[k][m] = r * base_amt
                    val = r * base_amt * infl(k, m)
                else:
                    val = 0.0
            v[k][m] = val
        for k in ("legacy_income_adj", "legacy_cogs_other", "legacy_expenses_other"):
            v[k][m] = 0.0

    # ---- sums, totals, margins (every month)
    for m in MONTHS:
        for parent in ("gross_sales", "cogs_product", "fulfillment", "seller_fees_total", "marketing_agency_total", "marketing_media_total",
                       "software_total"):
            v[parent][m] = _sum(v, CHILDREN[parent], m)
        v["marketing_total"][m] = v["marketing_agency_total"][m] + v["marketing_media_total"][m]
        v["overhead_total"][m] = (_sum(v, ("accounting", "chargeback_management", "consulting", "customer_service", "legal", "office_supplies",
                                           "bank_fees"), m) + v["software_total"][m])
        for k in ("legacy_income_adj", "legacy_cogs_other", "legacy_expenses_other"):
            v[k].setdefault(m, 0.0)
        v["total_income"][m] = v["gross_sales"][m] + v["shipping_income"].get(m, 0.0) + v["refunds_discounts"].get(m, 0.0) + v["legacy_income_adj"][m]
        v["total_cogs"][m] = v["cogs_product"][m] + v["fulfillment"][m] + v["seller_fees_total"][m] + v["legacy_cogs_other"][m]
        v["gross_profit"][m] = v["total_income"][m] - v["total_cogs"][m]
        v["total_expenses"][m] = v["marketing_total"][m] + v["overhead_total"][m] + v["legacy_expenses_other"][m]
        v["noi"][m] = v["gross_profit"][m] - v["total_expenses"][m]
        v["net_other_income"][m] = v["other_income"].get(m, 0.0) - v["interest_expense"].get(m, 0.0)
        v["net_income"][m] = v["noi"][m] + v["net_other_income"][m]
        for rk, (num, den) in RATIOS.items():
            d = v[den].get(m, 0.0)
            if d:
                v[rk][m] = v[num].get(m, 0.0) / d
    _trailing(v, int(settings["trend"]["avg_window"]))

    legacy = legacy_lines(v)
    out.lines = {**legacy, **v}
    out.fy = _fiscal_years(out.lines, v)
    out.fy_flat = {f"{y}.{k}": val for y, row in out.fy.items() for k, val in row.items()}
    out.ttm = dict(v["ttm_noi"])
    out.ftm = dict(v["ftm_noi"])
    out.ev = M.implied_ev(out.fy, settings["ev"])
    out.driver_info = _describe(defaults, resolve_rate, books_mean, n_window)
    out.cogs_model = {}
    return out


def _trailing(v: dict[str, Series], avg_window: int) -> None:
    """The sheet chart's trailing, forward and growth rows on total income, net operating income and DTC sales (same key names)."""
    shim: dict[str, Series] = {"total_revenue": v["total_income"], "noi": v["noi"], "dtc_rev": v["dtc_sales"]}
    for k in _SHEET_TRAIL:
        shim[k] = v[k]
    M.trailing_metrics(shim, avg_window)


def _fiscal_years(full: dict[str, Series], v: dict[str, Series]) -> dict[int, dict[str, float]]:
    """Calendar-year roll-up: sums for money lines, ratios recomputed from the sums, for the new lines and the legacy view alike."""
    out: dict[int, dict[str, float]] = {}
    for y in sorted({m.year for m in MONTHS}):
        months = [m for m in MONTHS if m.year == y]
        row = {k: sum(s.get(m, 0.0) for m in months) for k, s in full.items()
               if not (k in L.LINE_KEYS and L.LINE_KEYS[k].kind in ("ratio", "trail")) and not (k in LINE_KEYS and k in NON_SUMMED)}
        for rk, (num, den) in {**L.RATIOS, **RATIOS}.items():
            if row.get(den):
                row[rk] = row.get(num, 0.0) / row[den]
        row["ttm_noi"] = v["ttm_noi"].get(months[-1], 0.0)
        row["ttm_revenue"] = v["ttm_revenue"].get(months[-1], 0.0)
        out[y] = row
    return out


def _describe(defaults, resolve_rate, books_mean, n_window) -> dict[str, dict]:
    info: dict[str, dict] = {}
    for k in DRIVER_KEYS:
        segs = defaults.get(k) or []
        seg = segs[-1] if segs else None
        e: dict = {"segments": segs, "mode": seg["mode"] if seg else None}
        if seg:
            if seg["mode"] == "fixed":
                e["value"] = seg.get("value", 0.0)
            elif seg["mode"] == "rate":
                r, months = resolve_rate(k, seg)
                e.update(base=seg["base"], rate_kind=seg.get("rate_kind", "window"), rate=r, window=[x.isoformat() for x in months])
            elif seg["mode"] == "books_mean":
                val, months = books_mean(k, seg)
                e.update(value=val, window=[x.isoformat() for x in months], window_months=seg.get("window") or n_window)
            elif seg["mode"] == "avg":
                e["window_months"] = seg["window"]
        info[k] = e
    return info


# ---------------------------------------------------------------------------------------------------------------- the view other modules read
def legacy_lines(v: dict[str, Series]) -> dict[str, Series]:
    """Every old LINE_KEY as a Series built from the new lines, so pacing, financing and the modeler (which read old keys by name) keep working.
    Months where none of the sources has a value stay blank. total_revenue = total income; the sales-tax slot = shipping income + refunds and discounts
    (+ old-format adjustments), so revenue ties; interest expense and other income are not part of the legacy overhead."""
    def add(*keys: str, sign: tuple[int, ...] | None = None) -> Series:
        s: Series = {}
        for i, k in enumerate(keys):
            sg = 1 if sign is None else sign[i]
            for m, val in v[k].items():
                s[m] = s.get(m, 0.0) + sg * val
        return s

    out: dict[str, Series] = {k: {} for k in L.LINE_KEYS}
    out.update({
        "nc_rev": dict(v["nc_rev"]), "repeat_rev": dict(v["repeat_rev"]),
        "dtc_rev": dict(v["dtc_sales"]), "amazon_rev": dict(v["amazon_sales"]), "tiktok_rev": dict(v["tiktok_sales"]),
        "sales_tax": add("shipping_income", "refunds_discounts", "legacy_income_adj"),
        "total_revenue": dict(v["total_income"]),
        "amazon_fba_fees": dict(v["fba_fees"]), "amazon_seller_fees": add("amazon_fees", "tiktok_fees"),
        "amazon_fees": add("fba_fees", "amazon_fees", "tiktok_fees"),
        "cogs": dict(v["cogs_product"]), "china_cogs": dict(v["cogs_product"]),
        "fulfillment_services": dict(v["threepl_services"]), "inbound_shipping": dict(v["legacy_cogs_other"]),
        "seller_fees": add("adyen_fees", "paypal_fees"), "shopify_subscriptions": dict(v["shopify_fees"]),
        "payment_processing": add("adyen_fees", "paypal_fees", "shopify_fees"),
        "marketing_spend": dict(v["marketing_media_total"]),
        "mkt_amazon_ads": dict(v["amazon_ads"]), "mkt_facebook_ads": dict(v["facebook_ads"]), "mkt_google_ads": dict(v["google_ads"]),
        "mkt_tiktok_ads": dict(v["tiktok_ads"]),
        "marketing": add("marketing_agency_total", "software_marketing"), "marketing_agency": dict(v["marketing_agency_total"]),
        "marketing_software": dict(v["software_marketing"]),
        "other_business_expenses": dict(v["bank_fees"]), "bank_fees": dict(v["bank_fees"]),
        "overhead": add("accounting", "chargeback_management", "consulting", "customer_service", "legal", "office_supplies", "software_operations"),
        "accounting": dict(v["accounting"]), "chargeback_management": dict(v["chargeback_management"]), "consulting": dict(v["consulting"]),
        "customer_service": dict(v["customer_service"]), "legal": dict(v["legal"]), "office_admin": dict(v["office_supplies"]),
        "software_apps": dict(v["software_operations"]), "uncategorized": dict(v["legacy_expenses_other"]),
        "total_cogs": dict(v["total_cogs"]), "gross_profit": dict(v["gross_profit"]), "total_expenses": dict(v["total_expenses"]), "noi": dict(v["noi"]),
    })
    out["non_media_expenses"] = {m: val - out["marketing_spend"].get(m, 0.0) for m, val in out["total_expenses"].items()}
    for rk, (num, den) in L.RATIOS.items():
        for m in MONTHS:
            d = out[den].get(m, 0.0)
            if d:
                out[rk][m] = out[num].get(m, 0.0) / d
    return out
