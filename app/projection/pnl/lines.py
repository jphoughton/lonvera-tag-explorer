"""P&L line vocabulary: one stable snake_case key per sheet row of `P&L` (Lonvera Projections), shared with downstream modules.

`LINE_KEYS` (ordered dict key -> Line) is the contract: keys never change once published. `ROW_TO_KEY` maps a sheet row number to its key.
`PnlResult.lines` holds a monthly Series for EVERY key below (subtotals, ratios, TTM/FTM and growth rows included) on the 60-month axis.

Kinds
    qb        leaf account with QuickBooks history (proj_actuals line_key 'pnl.<key>'), projected by a driver in app/projection/pnl/model.py
    upstream  comes from the drivers / cohorts modules (never stored here except the revenue accounts marked qb)
    sum       sum of its children
    calc      formula of other lines
    ratio     ratio of two lines (recomputed, never summed, in fiscal-year columns)
    trail     trailing / forward / growth metric
The `_6m` key suffix is historical: those rows are rolling averages over `settings.trend.avg_window` months (6 by default).
Row 34 of the sheet (unlabelled, always blank) and the scratch cells in rows 164-169 are not lines. Inventory shrinkage has no sheet row of its own
(the sheet adds $22,631.40 inside the Feb-25 total, cell D46): it is a COGS line here and `row` is None.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    key: str
    label: str
    row: int | None
    kind: str
    fmt: str = "usd0"                  # grid format: usd0 | pct | num
    parent: str | None = None
    indent: int = 0
    note: str | None = None


def _l(key, label, row, kind, fmt="usd0", parent=None, indent=0, note=None) -> Line:
    return Line(key, label, row, kind, fmt, parent, indent, note)


_LINES: list[Line] = [
    # revenue (rows 12-22; the plan behind them is the drivers module)
    _l("nc_rev", "New-customer revenue", 12, "upstream"),
    _l("repeat_rev", "Repeat revenue", 17, "upstream"),
    _l("dtc_rev", "Total DTC revenue", 18, "qb"),
    _l("amazon_rev", "Amazon revenue", 19, "qb"),
    _l("tiktok_rev", "TikTok revenue", 20, "qb"),
    _l("sales_tax", "Sales tax collected (estimate)", 21, "upstream"),
    _l("total_revenue", "Total company revenue", 22, "calc"),
    # Amazon fees
    _l("amazon_fees", "Amazon fees", 24, "sum"),
    _l("amazon_fba_fees", "Amazon fulfillment (FBA) fees", 25, "qb", parent="amazon_fees", indent=1),
    _l("amazon_seller_fees", "Amazon seller fees and charges", 26, "qb", parent="amazon_fees", indent=1),
    _l("amazon_subscriptions", "Amazon subscriptions", 27, "qb", parent="amazon_fees", indent=1),
    _l("amazon_inbound_transport", "Amazon inbound transportation fees", 28, "qb", parent="amazon_fees", indent=1),
    _l("amazon_fees_pct", "Amazon fees % of Amazon revenue", 29, "ratio", "pct"),
    # cost of goods sold
    _l("cogs", "Cost of goods sold", 31, "sum"),
    _l("china_cogs", "China COGS", 32, "qb", parent="cogs", indent=1),
    _l("us_cogs", "US COGS", 33, "qb", parent="cogs", indent=1),
    _l("inventory_shrinkage", "Inventory shrinkage", None, "qb"),
    _l("direct_product_cost_pct", "Direct product cost % of revenue", 35, "ratio", "pct"),
    _l("fulfillment_services", "Fulfillment services", 37, "qb"),
    _l("inbound_shipping", "Inbound shipping", 38, "qb"),
    _l("payment_processing", "Payment processing fees", 40, "sum"),
    _l("shipping_supplies", "Shipping supplies", 42, "qb", parent="payment_processing", indent=1),
    _l("shopify_subscriptions", "Shopify subscriptions", 43, "qb", parent="payment_processing", indent=1),
    _l("seller_fees", "Seller fees", 44, "qb", parent="payment_processing", indent=1),
    _l("total_cogs", "Total cost of goods sold", 46, "calc"),
    _l("gross_profit", "Gross profit", 47, "calc"),
    _l("gross_profit_pct", "Gross profit %", 48, "ratio", "pct"),
    # marketing spend (the plan behind it is the drivers module)
    _l("marketing_spend", "Marketing spend", 51, "sum"),
    _l("mkt_amazon_ads", "Amazon ads", 52, "qb", parent="marketing_spend", indent=1),
    _l("mkt_facebook_ads", "Facebook ads", 53, "qb", parent="marketing_spend", indent=1),
    _l("mkt_google_ads", "Google ads", 54, "qb", parent="marketing_spend", indent=1),
    _l("mkt_bing_ads", "Microsoft Bing ads", 55, "qb", parent="marketing_spend", indent=1),
    _l("mkt_tiktok_ads", "TikTok ads", 56, "qb", parent="marketing_spend", indent=1),
    _l("marketing_spend_pct", "Marketing spend % of revenue", 57, "ratio", "pct"),
    # marketing operating expenses
    _l("marketing", "Marketing", 59, "sum"),
    _l("marketing_agency", "Marketing agency", 60, "qb", parent="marketing", indent=1),
    _l("marketing_services", "Marketing services", 61, "qb", parent="marketing", indent=1),
    _l("marketing_influencers", "Marketing influencers", 62, "qb", parent="marketing", indent=1),
    _l("marketing_software", "Marketing software", 63, "qb", parent="marketing", indent=1),
    _l("marketing_opex_pct", "Marketing operating expenses % of revenue", 64, "ratio", "pct"),
    # other business expenses
    _l("other_business_expenses", "Other business expenses", 66, "sum"),
    _l("other_direct", "Other business expenses (direct)", 67, "qb", parent="other_business_expenses", indent=1),
    _l("bank_fees", "Bank fees", 68, "qb", parent="other_business_expenses", indent=1),
    _l("fines_penalties", "Fines and penalties", 69, "qb", parent="other_business_expenses", indent=1),
    _l("meals", "Meals", 70, "qb", parent="other_business_expenses", indent=1),
    _l("miscellaneous", "Miscellaneous", 71, "qb", parent="other_business_expenses", indent=1),
    _l("subscriptions", "Subscriptions", 72, "qb", parent="other_business_expenses", indent=1),
    _l("travel", "Travel", 73, "qb", parent="other_business_expenses", indent=1),
    # headcount
    _l("headcount", "Headcount", 75, "qb"),
    _l("headcount_pct", "Headcount % of revenue", 76, "ratio", "pct"),
    # overhead
    _l("overhead", "Overhead expenses", 78, "sum"),
    _l("accounting", "Accounting", 79, "qb", parent="overhead", indent=1),
    _l("chargeback_management", "Chargeback management", 80, "qb", parent="overhead", indent=1),
    _l("consulting", "Consulting", 81, "qb", parent="overhead", indent=1),
    _l("customer_service", "Customer service", 82, "qb", parent="overhead", indent=1),
    _l("insurance", "Insurance", 83, "qb", parent="overhead", indent=1),
    _l("interest_expense", "Interest expense", 84, "qb", parent="overhead", indent=1),
    _l("legal", "Legal", 85, "qb", parent="overhead", indent=1),
    _l("memberships", "Membership and subscriptions", 86, "qb", parent="overhead", indent=1),
    _l("office_admin", "Office and general administrative", 87, "qb", parent="overhead", indent=1),
    _l("outside_services", "Outside services", 88, "qb", parent="overhead", indent=1),
    _l("sales_tax_filing", "Sales tax filing services", 89, "qb", parent="overhead", indent=1),
    _l("samples", "Samples", 90, "qb", parent="overhead", indent=1),
    _l("software_apps", "Software and apps", 91, "qb", parent="overhead", indent=1),
    _l("overhead_pct", "Overhead % of revenue", 92, "ratio", "pct"),
    _l("uncategorized", "Uncategorized expense", 94, "qb"),
    # totals
    _l("total_expenses", "Total expenses", 95, "calc"),
    _l("expense_pct", "Expenses % of revenue", 96, "ratio", "pct"),
    _l("non_media_expenses", "Non-media expenses", 97, "calc"),
    _l("non_media_pct", "Non-media expenses % of revenue", 98, "ratio", "pct"),
    _l("noi", "Net operating income", 100, "calc"),
    _l("noi_margin", "Net operating income margin", 101, "ratio", "pct"),
    # forward and trailing metrics
    _l("ftm_revenue", "Forward revenue run-rate (month x 12)", 135, "trail"),
    _l("ftm_noi", "Forward net operating income run-rate (month x 12)", 136, "trail"),
    _l("ttm_revenue", "Trailing 12-month revenue", 139, "trail"),
    _l("ttm_noi", "Trailing 12-month net operating income", 140, "trail"),
    _l("ttm_noi_margin", "Trailing 12-month net operating income margin", 141, "trail", "pct"),
    # growth
    _l("dtc_rev_mom", "Total DTC revenue, month over month", 144, "trail", "pct"),
    _l("dtc_rev_mom_6m", "Total DTC revenue, month over month (rolling average)", 145, "trail", "pct"),
    _l("dtc_rev_yoy", "Total DTC revenue, year over year", 146, "trail", "pct"),
    _l("dtc_rev_yoy_6m", "Total DTC revenue, year over year (rolling average)", 147, "trail", "pct"),
    _l("total_rev_mom", "Total company revenue, month over month", 149, "trail", "pct"),
    _l("total_rev_mom_6m", "Total company revenue, month over month (rolling average)", 150, "trail", "pct"),
    _l("total_rev_yoy", "Total company revenue, year over year", 151, "trail", "pct"),
    _l("total_rev_yoy_6m", "Total company revenue, year over year (rolling average)", 152, "trail", "pct"),
    _l("noi_mom", "Net operating income, month over month", 154, "trail", "pct"),
    _l("noi_mom_6m", "Net operating income, month over month (rolling average)", 155, "trail", "pct"),
    _l("noi_yoy", "Net operating income, year over year", 156, "trail", "pct"),
    _l("noi_yoy_6m", "Net operating income, year over year (rolling average)", 157, "trail", "pct"),
]

LINE_KEYS: dict[str, Line] = {ln.key: ln for ln in _LINES}
ROW_TO_KEY: dict[int, str] = {ln.row: ln.key for ln in _LINES if ln.row is not None}
assert len(LINE_KEYS) == len(_LINES), "duplicate line key"
assert len(ROW_TO_KEY) == sum(1 for ln in _LINES if ln.row is not None), "duplicate sheet row"

# QuickBooks leaves: stored in proj_actuals as 'pnl.<key>'
QB_KEYS: tuple[str, ...] = tuple(ln.key for ln in _LINES if ln.kind == "qb")
ACTUALS_PREFIX = "pnl."

# Leaves a driver projects (everything with QuickBooks history except the revenue accounts and marketing_spend channels, which the
# drivers module supplies)
REVENUE_KEYS = ("dtc_rev", "amazon_rev", "tiktok_rev")
CHANNEL_KEYS = ("mkt_amazon_ads", "mkt_facebook_ads", "mkt_google_ads", "mkt_bing_ads", "mkt_tiktok_ads")
# other_business_expenses is a sum of its sub-lines in actual months and ONE driven line (rate x revenue) in projected months; its sub-lines are
# blank when projected, so they take no driver
DRIVEN_PARENTS = ("other_business_expenses",)

CHILDREN: dict[str, tuple[str, ...]] = {}
for _ln in _LINES:
    if _ln.parent:
        CHILDREN.setdefault(_ln.parent, tuple())
        CHILDREN[_ln.parent] = CHILDREN[_ln.parent] + (_ln.key,)
DRIVER_KEYS: tuple[str, ...] = tuple(k for k in QB_KEYS if k not in REVENUE_KEYS and k not in CHANNEL_KEYS
                                     and k not in CHILDREN["other_business_expenses"])

# ratio -> (numerator, denominator); fiscal-year columns recompute these from the summed parts
RATIOS: dict[str, tuple[str, str]] = {
    "amazon_fees_pct": ("amazon_fees", "amazon_rev"),
    "direct_product_cost_pct": ("china_cogs", "total_revenue"),
    "gross_profit_pct": ("gross_profit", "total_revenue"),
    "marketing_spend_pct": ("marketing_spend", "total_revenue"),
    "marketing_opex_pct": ("marketing", "total_revenue"),
    "headcount_pct": ("headcount", "total_revenue"),
    "overhead_pct": ("overhead", "total_revenue"),
    "expense_pct": ("total_expenses", "total_revenue"),
    "non_media_pct": ("non_media_expenses", "total_revenue"),
    "noi_margin": ("noi", "total_revenue"),
}

# Display order of the P&L page: (header label, [keys]); keys not listed here are not shown on the main grid
SECTIONS: list[tuple[str, list[str]]] = [
    ("Revenue", ["nc_rev", "repeat_rev", "dtc_rev", "amazon_rev", "tiktok_rev", "sales_tax", "total_revenue"]),
    ("Cost of goods sold", ["amazon_fees", "amazon_fba_fees", "amazon_seller_fees", "amazon_subscriptions", "amazon_inbound_transport",
                            "cogs", "china_cogs", "us_cogs", "inventory_shrinkage", "fulfillment_services", "inbound_shipping",
                            "payment_processing", "shipping_supplies", "shopify_subscriptions", "seller_fees", "total_cogs"]),
    ("Gross profit", ["gross_profit", "gross_profit_pct"]),
    ("Marketing spend", ["marketing_spend", "mkt_amazon_ads", "mkt_facebook_ads", "mkt_google_ads", "mkt_bing_ads", "mkt_tiktok_ads",
                         "marketing_spend_pct"]),
    ("Marketing", ["marketing", "marketing_agency", "marketing_services", "marketing_influencers", "marketing_software"]),
    ("Other business expenses", ["other_business_expenses", "other_direct", "bank_fees", "fines_penalties", "meals", "miscellaneous",
                                 "subscriptions", "travel"]),
    ("Headcount", ["headcount"]),
    ("Overhead", ["overhead", "accounting", "chargeback_management", "consulting", "customer_service", "insurance", "interest_expense", "legal",
                  "memberships", "office_admin", "outside_services", "sales_tax_filing", "samples", "software_apps"]),
    ("Other", ["uncategorized"]),
    ("Net operating income", ["total_expenses", "non_media_expenses", "noi", "noi_margin"]),
]

# Keys whose fiscal-year column is a sum of the months (everything money-valued except trailing/forward/growth metrics)
NON_SUMMED = frozenset(k for k, ln in LINE_KEYS.items() if ln.kind in ("ratio", "trail"))


def actuals_key(key: str) -> str:
    return ACTUALS_PREFIX + key


def series_key(key: str) -> str:
    """proj_series line_key for a monthly override of a line."""
    return ACTUALS_PREFIX + key
