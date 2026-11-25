"""The sheet chart as a ChartDef: a view over app/projection/pnl/lines.py, which stays the one definition of the sheet's lines."""
from __future__ import annotations

from .. import lines as L
from . import ChartDef, SHEET

DRIVER_GROUPS: list[tuple[str, list[str]]] = [
    ("Amazon fees", ["amazon_fba_fees", "amazon_seller_fees", "amazon_subscriptions", "amazon_inbound_transport"]),
    ("Cost of goods sold", ["china_cogs", "us_cogs", "inventory_shrinkage", "fulfillment_services", "inbound_shipping", "shipping_supplies",
                            "shopify_subscriptions", "seller_fees"]),
    ("Marketing", ["marketing_agency", "marketing_services", "marketing_influencers", "marketing_software"]),
    ("Other business expenses", ["other_business_expenses"]),
    ("Headcount", ["headcount"]),
    ("Overhead", ["accounting", "chargeback_management", "consulting", "customer_service", "insurance", "interest_expense", "legal", "memberships",
                  "office_admin", "outside_services", "sales_tax_filing", "samples", "software_apps"]),
    ("Other", ["uncategorized"]),
]

# Account families a closed month must carry before it can become books (membership is the structure of the P&L; which are required is a setting).
FAMILIES: dict[str, tuple[str, ...]] = {
    "revenue": L.REVENUE_KEYS,
    "cost_of_goods": ("china_cogs", "us_cogs"),
    "fulfillment": ("fulfillment_services", "inbound_shipping"),
    "marketing_spend": L.CHANNEL_KEYS,
    "operating_expenses": tuple(k for k in L.QB_KEYS if k not in L.REVENUE_KEYS and k not in L.CHANNEL_KEYS and k not in
                                ("china_cogs", "us_cogs", "fulfillment_services", "inbound_shipping", "amazon_fba_fees", "amazon_seller_fees",
                                 "amazon_subscriptions", "amazon_inbound_transport", "shipping_supplies", "shopify_subscriptions", "seller_fees",
                                 "inventory_shrinkage")),
}
FAMILY_LABEL = {"revenue": "revenue", "cost_of_goods": "cost of goods", "fulfillment": "fulfillment and shipping", "marketing_spend": "ad spend",
                "operating_expenses": "operating expenses"}

CHART = ChartDef(
    name=SHEET, label="Sheet chart (old format)", line_keys=L.LINE_KEYS, qb_keys=L.QB_KEYS, actuals_prefix=L.ACTUALS_PREFIX, series_prefix=L.ACTUALS_PREFIX,
    driver_keys=L.DRIVER_KEYS, driven_parents=L.DRIVEN_PARENTS, channel_keys=L.CHANNEL_KEYS + ("marketing_spend",),
    bases=("amazon_rev", "dtc_rev", "total_revenue"), modes=("fixed", "carry", "avg", "rate", "headcount"),
    rate_kinds=("window", "ratio_avg", "typed", "cogs_model"), sections=L.SECTIONS, driver_groups=DRIVER_GROUPS, income_keys=L.REVENUE_KEYS,
    families=FAMILIES, family_label=FAMILY_LABEL, params_ns=("drivers",), total_keys=("total_revenue", "gross_profit", "noi"),
    subtotal_calc=("total_cogs", "total_expenses", "non_media_expenses"),
    trend_keys=("ttm_revenue", "ttm_noi", "ttm_noi_margin", "ftm_revenue", "ftm_noi", "dtc_rev_mom", "dtc_rev_mom_6m", "dtc_rev_yoy", "dtc_rev_yoy_6m",
                "total_rev_mom", "total_rev_mom_6m", "total_rev_yoy", "total_rev_yoy_6m", "noi_mom", "noi_mom_6m", "noi_yoy", "noi_yoy_6m"))
