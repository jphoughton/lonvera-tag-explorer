"""Declarative metric catalog for the pacing rollups (day, ISO week, month).

Every metric is data: key, label, band, format and a function of the period's summed base measures `b` (and the metrics already computed `v`).
Additive base measures are summed over the days of a period; ratios are recomputed from the sums (never averaged). Reach-type bases
(impressions, reach, unique outbound clicks, unique clicks by product) are not additive: for a week or a month they are read from the feed rows pulled
at that grain. Adding a band or a metric means adding rows here; the engine, API and page read this list.

Keys follow the sheet's machine keys (row 3 of Pacing - Daily / Rollup tabs) so the two can be compared column by column.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

PRODUCTS = (("vitabull", "Vital Bull", "bull"), ("beefliver", "Beef Liver", "liver"), ("boneflex", "Boneflex", "bone"))
PRODUCT_FEED = {"vitabull": "VitaBull", "beefliver": "Beef Liver", "boneflex": "Boneflex"}
META_GROUPS = (("all", "All Meta"), ("bone", "Boneflex"), ("bull", "Vital Bull"), ("liver", "Beef Liver"), ("scale", "Scaling"), ("test", "Testing"))

# Base measures that are NOT additive over days: taken from the feed rows of the period's own grain.
REACH_BASES = ("r_impressions", "r_reach", "r_uoc", "pu_vitabull", "pu_beefliver", "pu_boneflex")


def div(n, d):
    """n / d, None when d is empty/zero or n is missing (the sheet shows a blank)."""
    if n is None or d is None or d == 0:
        return None
    return n / d


def _sub(a, b):
    return None if a is None or b is None else a - b


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    band: str                       # section header in the grid
    fmt: str                        # money | money2 | int | num2 | pct | pct1 | x
    fn: Callable[[dict, dict], float | None] = field(compare=False, repr=False)
    note: str = ""                  # short source / formula shown on hover
    kind: str = "line"              # line | ratio | subtotal
    additive: bool = False          # a plain sum over the period (can be projected by days; ratios cannot)
    sub: str = ""                   # sub-band (a product / stage) inside the band
    views: tuple[str, ...] = ("rollup",)    # which grids list it: rollup | meta
    short: str = ""                 # row label inside a sub-band (a product name), when the full label repeats the sub-band


CATALOG: list[Metric] = []
BY_KEY: dict[str, Metric] = {}


def _add(m: Metric) -> Metric:
    if m.key in BY_KEY:
        raise ValueError(f"duplicate metric {m.key}")
    CATALOG.append(m)
    BY_KEY[m.key] = m
    return m


def S(key, label, band, fmt, expr, note="", sub="", views=("rollup",), kind="line"):
    """Additive metric: `expr` is a function of the bases only."""
    return _add(Metric(key, label, band, fmt, lambda b, v, e=expr: e(b), note, kind, True, sub, views))


def R(key, label, band, fmt, expr, note="", sub="", views=("rollup",)):
    """Ratio metric: `expr(b, v)` may use bases and metrics computed above it."""
    return _add(Metric(key, label, band, fmt, expr, note, "ratio", False, sub, views))


def B(key, label, band, fmt, base, note="", sub="", views=("rollup",), additive=True):
    """A base measure shown as is."""
    return _add(Metric(key, label, band, fmt, (lambda b, v, k=base: b[k]) if additive else (lambda b, v, k=base: b.get(k)), note, "line", additive, sub, views))


# ---------------------------------------------------------------------------------------------------------------- business-wide
BW = "Business-wide"
S("biz_total_rev", "Total revenue", BW, "money", lambda b: b["o_total_rev"] + b["a_revenue"], "Shopify total revenue plus Amazon revenue (gross of sales tax)")
S("biz_total_spend", "Total ad spend", BW, "money", lambda b: b["pl_nonmeta_spend"] + b["m_spend"] + b["a_ad_spend"],
  "Meta (Graph API) + Google + TikTok + other platforms + Amazon ads")
S("biz_nc_rev", "New-customer revenue", BW, "money", lambda b: b["o_nc_rev"] + b["a_nc_rev"], "Shopify new-customer revenue plus Amazon new-customer revenue")
R("biz_nc_roas", "NC ROAS", BW, "x", lambda b, v: div(v["biz_nc_rev"], v["biz_total_spend"]), "New-customer revenue / total ad spend")
R("biz_nc_cvr", "NC conversion", BW, "pct", lambda b, v: div(v["biz_nc_orders"], b["m_link_clicks"] + b["g_clicks"] + b["a_clicks"]),
  "New customers / (Meta link clicks + Google clicks + Amazon clicks)")
R("biz_nc_cpa", "NC CPA", BW, "money2", lambda b, v: div(v["biz_total_spend"], v["biz_nc_orders"]), "Total ad spend / new customers")
R("biz_nc_aov", "NC AOV", BW, "money2", lambda b, v: div(v["biz_nc_rev"], v["biz_nc_orders"]), "New-customer revenue / new customers")
S("biz_nc_orders", "New customers", BW, "int", lambda b: b["o_nc_orders"] + b["a_nc_orders"], "Shopify new customers plus Amazon new customers")
R("biz_mer", "MER", BW, "x", lambda b, v: div(v["biz_total_rev"], v["biz_total_spend"]), "Total revenue / total ad spend (marketing efficiency ratio)")
S("biz_repeat_rev", "Repeat revenue", BW, "money", lambda b: b["o_rc_rev"] + b["a_revenue"] - b["a_nc_rev"], "Shopify returning revenue plus Amazon revenue not from new customers")
R("biz_repeat_share", "Repeat share", BW, "pct1", lambda b, v: div(v["biz_repeat_rev"], v["biz_total_rev"]), "Repeat revenue / total revenue")
S("biz_total_rev_net", "Total revenue, net of sales tax and refunds", BW, "money", lambda b: b["o_total_rev"] - b["o_tax"] - b["o_refunds"] + b["a_revenue"],
  "Shopify total less tax and refunds, plus Amazon")
S("biz_total_rev_plan", "Total revenue, plan basis", BW, "money", lambda b: b["o_total_rev"] - b["x_tax_est"] + b["a_revenue"],
  "Shopify total less the plan's sales-tax estimate, plus Amazon: the basis of the P&L revenue goal")

# ---------------------------------------------------------------------------------------------------------------- DTC / Shopify
D = "DTC / Shopify"
B("dtc_total_rev", "Total revenue", D, "money", "o_total_rev", "Shopify total revenue")
B("dtc_nc_rev", "New-customer revenue", D, "money", "o_nc_rev", "Shopify new-customer revenue")
B("dtc_repeat_rev", "Repeat revenue", D, "money", "o_rc_rev", "Shopify returning-customer revenue")
B("dtc_total_orders", "Orders", D, "int", "o_total_orders", "Shopify order count")
B("dtc_nc_orders", "New customers", D, "int", "o_nc_orders", "Shopify new customers")
R("dtc_nc_cvr_visitors", "NC conversion (new visitors)", D, "pct", lambda b, v: div(b["o_nc_orders"], b["t_new_visitors"]), "New customers / pixel-tracked new visitors")
R("dtc_aov", "AOV", D, "money2", lambda b, v: div(b["o_total_rev"], b["o_total_orders"]), "Total revenue / orders")
R("dtc_nc_aov", "NC AOV", D, "money2", lambda b, v: div(b["o_nc_rev"], b["o_nc_orders"]), "New-customer revenue / new customers")
S("dtc_ad_spend", "DTC ad spend", D, "money", lambda b: b["m_spend"] + b["g_spend"] + b["tt_spend"], "Meta (Graph API) + Google + TikTok platform spend")
R("dtc_nc_cpa", "NC CPA", D, "money2", lambda b, v: div(v["dtc_ad_spend"], b["o_nc_orders"]), "DTC ad spend / new customers")
R("dtc_nc_roas", "NC ROAS", D, "x", lambda b, v: div(b["o_nc_rev"], v["dtc_ad_spend"]), "New-customer revenue / DTC ad spend")
R("dtc_mer", "MER", D, "x", lambda b, v: div(b["o_total_rev"], v["dtc_ad_spend"]), "Total revenue / DTC ad spend")
R("dtc_nc_cvr", "NC conversion", D, "pct", lambda b, v: div(b["o_nc_orders"], b["m_link_clicks"] + b["g_clicks"]), "New customers / (Meta link clicks + Google clicks)")
R("dtc_repeat_share", "Repeat share", D, "pct1", lambda b, v: div(b["o_rc_rev"], b["o_total_rev"]), "Returning revenue / total revenue")
B("dtc_new_visitors", "New visitors", D, "int", "t_new_visitors", "Upstack tracked new visitors (pixel, all sources)")
R("dtc_cost_per_new_visitor", "Cost per new visitor", D, "money2", lambda b, v: div(v["dtc_ad_spend"], b["t_new_visitors"]), "DTC ad spend / new visitors")
R("dtc_cost_per_uoc", "Cost per unique outbound click", D, "money2", lambda b, v: div(b["m_spend"], b["r_uoc"]), "Meta spend / Meta unique outbound clicks")

# ---------------------------------------------------------------------------------------------------------------- Meta
M_ = "Meta"
B("meta_spend", "Spend", M_, "money", "m_spend", "Meta campaign spend, both ad accounts (ad_daily)")
B("meta_link_clicks", "Link clicks", M_, "int", "m_link_clicks", "Meta link clicks")
R("meta_cpc", "CPC", M_, "money2", lambda b, v: div(b["m_spend"], b["m_link_clicks"]), "Spend / link clicks")
B("meta_purchases", "Purchases", M_, "int", "m_purchases", "Meta-reported purchases")
B("meta_purchase_value", "Purchase value", M_, "money", "m_value", "Meta-reported purchase value")
R("meta_cvr", "CVR", M_, "pct", lambda b, v: div(b["m_purchases"], b["m_link_clicks"]), "Purchases / link clicks")
R("meta_aov", "AOV", M_, "money2", lambda b, v: div(b["m_value"], b["m_purchases"]), "Purchase value / purchases")
B("meta_nc_orders", "NC orders (last-click)", M_, "int", "at_meta_nc_orders", "Upstack last-click attribution, Meta Ads")
B("meta_nc_rev", "NC revenue (last-click)", M_, "money", "at_meta_nc_rev", "Upstack last-click attribution, Meta Ads")
R("meta_nc_roas", "NC ROAS (last-click)", M_, "x", lambda b, v: div(b["at_meta_nc_rev"], b["m_spend"]), "Last-click NC revenue / spend")
R("meta_nc_cpa", "NC CPA (last-click)", M_, "money2", lambda b, v: div(b["m_spend"], b["at_meta_nc_orders"]), "Spend / last-click new customers")
R("meta_nc_aov", "NC AOV (last-click)", M_, "money2", lambda b, v: div(b["at_meta_nc_rev"], b["at_meta_nc_orders"]), "Last-click NC revenue / new customers")
R("meta_roas", "Platform ROAS", M_, "x", lambda b, v: div(b["m_value"], b["m_spend"]), "Meta-reported purchase value / spend")
B("meta_impressions", "Impressions", M_, "int", "r_impressions", "Meta Account - Reach (account level, both ad accounts)", additive=False)
B("meta_reach", "Reach", M_, "int", "r_reach", "Meta reach pulled at the period's own grain (not summed from days)", additive=False)
R("meta_first_impression_pct", "First-time impression share", M_, "pct1", lambda b, v: div(b.get("r_reach"), b.get("r_impressions")), "Reach / impressions")
R("meta_cost_per_1k_reach", "Cost per 1,000 reached", M_, "money2", lambda b, v: _scale(div(b["m_spend"], b.get("r_reach")), 1000), "Spend / reach x 1,000")
R("meta_cpm", "CPM", M_, "money2", lambda b, v: _scale(div(b["m_spend"], b.get("r_impressions")), 1000), "Spend / impressions x 1,000")
R("meta_frequency", "Frequency", M_, "num2", lambda b, v: div(b.get("r_impressions"), b.get("r_reach")), "Impressions / reach")
B("meta_unique_outbound_clicks", "Unique outbound clicks", M_, "int", "r_uoc", "People who clicked a link that left Meta, at the period's own grain", additive=False)
R("meta_unique_outbound_ctr", "Unique outbound CTR", M_, "pct", lambda b, v: div(b.get("r_uoc"), b.get("r_impressions")), "Unique outbound clicks / impressions")
R("meta_avg_cost_cap", "Average cost cap", M_, "money2", lambda b, v: div(b["b_spend_x_cap"], b["b_capped"]), "Spend-weighted bid amount on cost-cap / bid-cap ad sets")
R("meta_avg_roas_floor", "Average ROAS floor", M_, "num2", lambda b, v: div(b["b_spend_x_floor"], b["b_floor"]), "Spend-weighted minimum-ROAS floor")
R("meta_capped_spend_pct", "Spend on capped ad sets", M_, "pct1", lambda b, v: div(b["b_capped"], b["b_spend"]), "Spend in cost-cap / bid-cap ad sets / Meta ad set spend")
R("meta_pct_cost_cap", "Spend on cost cap", M_, "pct1", lambda b, v: div(b["b_capped"], b["b_spend"]), "Cost-cap / bid-cap spend / Meta ad set spend")
R("meta_pct_troas_floor", "Spend on ROAS floor", M_, "pct1", lambda b, v: div(b["b_floor"], b["b_spend"]), "Minimum-ROAS-floor spend / Meta ad set spend")


def _scale(x, k):
    return None if x is None else x * k


# ---------------------------------------------------------------------------------------------------------------- Meta by product (nine bands x three products + total)
PB = "Meta by product"
_TOTALS = ("Total", "prod")


def _prod_band(metric: str, label: str, fmt: str, note: str, per_product, total, additive: bool):
    for slug, name, _ in PRODUCTS:
        key = f"meta_{metric}_{slug}"
        _add(Metric(key, f"{name} {label}", PB, fmt, (lambda b, v, f=per_product, s=slug: f(b, v, s)), note,
                    "line" if additive else "ratio", additive, label, short=name))
    total_key = {"ncroas": "prod_nc_roas_total", "nccpa": "prod_nc_cpa_total"}.get(metric, f"prod_{metric}_total")      # the sheet's machine keys
    _add(Metric(total_key, f"Total {label}", PB, fmt, total, note, "subtotal" if additive else "ratio", additive, label, short="Total"))


_prod_band("spend", "spend", "money", "Meta spend by product (campaign, ad set or ad name)", lambda b, v, s: b[f"ps_{s}"], lambda b, v: b["m_spend"], True)
_prod_band("ncrev", "NC revenue", "money", "Actual Shopify first-order revenue by product, not Meta-attributed", lambda b, v, s: b[f"pn_rev_{s}"],
           lambda b, v: b["o_nc_rev"], True)
_prod_band("ncroas", "NC ROAS", "x", "Actual NC revenue / Meta spend", lambda b, v, s: div(b[f"pn_rev_{s}"], b[f"ps_{s}"]),
           lambda b, v: div(b["o_nc_rev"], b["m_spend"]), False)
_prod_band("nco", "NC orders", "int", "Actual new customers whose first order was this product", lambda b, v, s: b[f"pn_ord_{s}"], lambda b, v: b["o_nc_orders"], True)
_prod_band("nccpa", "NC CPA", "money2", "Meta spend / actual new customers", lambda b, v, s: div(b[f"ps_{s}"], b[f"pn_ord_{s}"]),
           lambda b, v: div(b["m_spend"], b["o_nc_orders"]), False)
_prod_band("uoc", "unique outbound clicks", "int", "De-duplicated across the product's ad sets (from 2026-07-05)", lambda b, v, s: _blank0(b.get(f"pu_{s}")),
           lambda b, v: b.get("r_uoc"), False)
_prod_band("cvr", "conv rate", "pct", "Actual new customers / unique outbound clicks", lambda b, v, s: div(b[f"pn_ord_{s}"], _blank0(b.get(f"pu_{s}"))),
           lambda b, v: div(b["o_nc_orders"], b.get("r_uoc")), False)
_prod_band("rpc", "NC revenue per unique outbound click", "money2", "Actual NC revenue / unique outbound clicks",
           lambda b, v, s: div(b[f"pn_rev_{s}"], _blank0(b.get(f"pu_{s}"))), lambda b, v: div(b["o_nc_rev"], b.get("r_uoc")), False)
_prod_band("cpuoc", "cost per unique outbound click", "money2", "Meta spend / unique outbound clicks",
           lambda b, v, s: div(b[f"ps_{s}"], _blank0(b.get(f"pu_{s}"))), lambda b, v: div(b["m_spend"], b.get("r_uoc")), False)


def _blank0(x):
    return x if x else None


# ---------------------------------------------------------------------------------------------------------------- testing vs scaling
TS = "Testing vs scaling"
B("stage_spend_scaling", "Scaling spend", TS, "money", "m_scaling", "Meta spend, campaigns without 'test' in the name")
B("stage_spend_testing", "Testing spend", TS, "money", "m_testing", "Meta spend, campaigns with 'test' in the name")
S("stage_spend_total", "Total spend", TS, "money", lambda b: b["m_scaling"] + b["m_testing"], "Scaling + testing", kind="subtotal")
R("stage_testing_pct", "Testing share of spend", TS, "pct1", lambda b, v: div(b["m_testing"], v["stage_spend_total"]), "Testing spend / total spend")
B("stage_nc_scaling", "Scaling NC", TS, "int", "ac_scaling", "Upstack any-click 30-day new customers, scaling campaigns")
B("stage_nc_testing", "Testing NC", TS, "int", "ac_testing", "Upstack any-click 30-day new customers, testing campaigns")
S("stage_nc_total", "Total NC", TS, "int", lambda b: b["ac_total"], "Upstack any-click 30-day new customers, all Meta campaigns", kind="subtotal")
R("stage_cpa_scaling", "Scaling NC CPA", TS, "money2", lambda b, v: div(b["m_scaling"], b["ac_scaling"]), "Scaling spend / scaling NC")
R("stage_cpa_testing", "Testing NC CPA", TS, "money2", lambda b, v: div(b["m_testing"], b["ac_testing"]), "Testing spend / testing NC")
R("stage_cpa_total", "Total NC CPA", TS, "money2", lambda b, v: div(v["stage_spend_total"], v["stage_nc_total"]), "Total spend / total NC")
R("stage_cpa_test_vs_scale", "Testing CPA vs scaling", TS, "pct1",
  lambda b, v: None if not v.get("stage_cpa_testing") or not v.get("stage_cpa_scaling") else v["stage_cpa_testing"] / v["stage_cpa_scaling"] - 1,
  "Testing NC CPA / scaling NC CPA - 1")

# ---------------------------------------------------------------------------------------------------------------- Google
G = "Google"
B("google_spend", "Spend", G, "money", "g_spend", "Platform - History, Google Ads")
B("google_clicks", "Clicks", G, "int", "g_clicks", "Platform - History, Google Ads")
R("google_cpc", "CPC", G, "money2", lambda b, v: div(b["g_spend"], b["g_clicks"]), "Spend / clicks")
B("google_purchases", "Purchases", G, "int", "g_purchases", "Platform-reported")
B("google_purchase_value", "Purchase value", G, "money", "g_value", "Platform-reported")
R("google_cvr", "CVR", G, "pct", lambda b, v: div(b["g_purchases"], b["g_clicks"]), "Purchases / clicks")
R("google_aov", "AOV", G, "money2", lambda b, v: div(b["g_value"], b["g_purchases"]), "Purchase value / purchases")
B("google_nc_orders", "NC orders (last-click)", G, "int", "at_google_nc_orders", "Upstack last-click attribution, Google Ads")
B("google_nc_rev", "NC revenue (last-click)", G, "money", "at_google_nc_rev", "Upstack last-click attribution, Google Ads")
R("google_nc_roas", "NC ROAS (last-click)", G, "x", lambda b, v: div(b["at_google_nc_rev"], b["g_spend"]), "NC revenue / spend")
R("google_nc_cpa", "NC CPA (last-click)", G, "money2", lambda b, v: div(b["g_spend"], b["at_google_nc_orders"]), "Spend / new customers")
R("google_nc_aov", "NC AOV (last-click)", G, "money2", lambda b, v: div(b["at_google_nc_rev"], b["at_google_nc_orders"]), "NC revenue / new customers")
R("google_roas", "Platform ROAS", G, "x", lambda b, v: div(b["g_value"], b["g_spend"]), "Purchase value / spend")

# ---------------------------------------------------------------------------------------------------------------- Amazon
A = "Amazon"
B("amz_ad_spend", "Ad spend", A, "money", "a_ad_spend", "Amazon Ads, deduped v3 > v2 > v1")
B("amz_clicks", "Clicks", A, "int", "a_clicks", "Amazon Ads clicks")
B("amz_impressions", "Impressions", A, "int", "a_impr", "Amazon Ads impressions")
R("amz_cpc", "CPC", A, "money2", lambda b, v: div(b["a_ad_spend"], b["a_clicks"]), "Ad spend / clicks")
B("amz_revenue", "Revenue", A, "money", "a_revenue", "Amazon.com + B2B, by purchase date")
B("amz_orders", "Orders", A, "int", "a_orders", "Distinct Amazon orders")
B("amz_units", "Units", A, "int", "a_units", "Units shipped")
R("amz_aov", "AOV", A, "money2", lambda b, v: div(b["a_revenue"], b["a_orders"]), "Revenue / orders")
B("amz_nc_orders", "NC orders", A, "int", "a_nc_orders", "Orders on the customer's first-purchase day")
B("amz_nc_rev", "NC revenue", A, "money", "a_nc_rev", "Revenue on the customer's first-purchase day")
R("amz_nc_roas", "NC ROAS", A, "x", lambda b, v: div(b["a_nc_rev"], b["a_ad_spend"]), "NC revenue / ad spend")
R("amz_nc_cpa", "NC CPA", A, "money2", lambda b, v: div(b["a_ad_spend"], b["a_nc_orders"]), "Ad spend / NC orders")
B("amz_attributed_sales_14d", "Attributed sales (14-day)", A, "money", "a_attr", "Amazon Ads attributed sales")
R("amz_ad_roas", "Ad ROAS", A, "x", lambda b, v: div(b["a_attr"], b["a_ad_spend"]), "Attributed sales / ad spend")
R("amz_tacos", "TACoS", A, "pct1", lambda b, v: div(b["a_ad_spend"], b["a_revenue"]), "Ad spend / revenue (total advertising cost of sales)")
R("amz_cvr", "CVR", A, "pct", lambda b, v: div(b["a_orders"], b["a_clicks"]), "Orders / clicks")

# ---------------------------------------------------------------------------------------------------------------- EBITDA estimate
E = "EBITDA estimate"
B("noi_target", "NOI target", E, "money", "e_noi_target", "P&L net operating income of the month, spread by day")
S("ebitda_revenue", "Revenue, net", E, "money", lambda b: b["o_total_rev"] - b["o_tax"] - b["o_refunds"] + b["a_revenue"],
  "Shopify total less tax and refunds, plus Amazon")
B("ebitda_cogs", "Cost of goods sold", E, "money", "e_cogs", "Current plan rates on daily revenue; Amazon subscription spread by day")
S("ebitda_ad_spend", "Ad spend", E, "money", lambda b: b["pl_nonmeta_spend"] + b["m_spend"] + b["a_ad_spend"] + b["e_tiktok_plan"],
  "Platform spend + Amazon Ads + TikTok plan spread by day")
B("ebitda_variable_opex", "Variable operating expenses", E, "money", "e_var_opex", "Plan rates on daily revenue")
B("ebitda_fixed_opex", "Fixed operating expenses", E, "money", "e_fixed_opex", "Plan monthly dollars spread by day")
S("ebitda_opex_total", "Operating expenses", E, "money", lambda b: b["e_var_opex"] + b["e_fixed_opex"], "Variable + fixed operating expenses")
B("below_ebitda_per_day", "Items below EBITDA", E, "money", "e_below", "Marketing influencers + uncategorized, spread by day")
R("ebitda_target", "EBITDA target", E, "money", lambda b, v: None if not b.get("e_target_days") else b["e_noi_target"] + b["e_below"], "NOI target plus items below EBITDA")
S("ebitda_est", "EBITDA estimate", E, "money", lambda b: b["o_total_rev"] - b["o_tax"] - b["o_refunds"] + b["a_revenue"] - b["e_cogs"]
  - (b["pl_nonmeta_spend"] + b["m_spend"] + b["a_ad_spend"] + b["e_tiktok_plan"]) - b["e_var_opex"] - b["e_fixed_opex"],
  "Revenue - COGS - ad spend - variable opex - fixed opex", kind="subtotal")
R("ebitda_gap", "Gap vs target", E, "money", lambda b, v: None if v.get("ebitda_target") is None else v["ebitda_est"] - v["ebitda_target"], "EBITDA estimate - EBITDA target")
R("ebitda_margin", "EBITDA margin", E, "pct1", lambda b, v: div(v["ebitda_est"], v["ebitda_revenue"]), "EBITDA estimate / net revenue")
R("ebitda_contribution_margin", "Contribution margin", E, "pct1",
  lambda b, v: div(v["ebitda_revenue"] - v["ebitda_cogs"] - v["ebitda_ad_spend"] - v["ebitda_variable_opex"], v["ebitda_revenue"]),
  "(Revenue - COGS - ad spend - variable opex) / revenue")
S("noi_est", "NOI estimate", E, "money", lambda b: b["o_total_rev"] - b["o_tax"] - b["o_refunds"] + b["a_revenue"] - b["e_cogs"]
  - (b["pl_nonmeta_spend"] + b["m_spend"] + b["a_ad_spend"] + b["e_tiktok_plan"]) - b["e_var_opex"] - b["e_fixed_opex"] - b["e_below"],
  "EBITDA estimate - items below EBITDA", kind="subtotal")
R("noi_gap", "NOI gap vs target", E, "money", lambda b, v: None if not b.get("e_target_days") else v["noi_est"] - b["e_noi_target"], "NOI estimate - NOI target")

# ---------------------------------------------------------------------------------------------------------------- Meta groups (Meta - Daily / Weekly / Monthly)
MG = "Meta campaigns"
for _g, _name in META_GROUPS:
    _band = f"{MG}: {_name}"
    _add(Metric(f"{_g}_spend", "Spend", _band, "money", (lambda b, v, g=_g: b[f"mg_{g}_spend"]), "Meta campaign spend", "line", True, _name, ("meta",)))
    _add(Metric(f"{_g}_clicks", "Link clicks", _band, "int", (lambda b, v, g=_g: b[f"mg_{g}_clicks"]), "Meta link clicks", "line", True, _name, ("meta",)))
    _add(Metric(f"{_g}_cpc", "CPC", _band, "money2", (lambda b, v, g=_g: div(b[f"mg_{g}_spend"], b[f"mg_{g}_clicks"])), "Spend / link clicks", "ratio", False, _name, ("meta",)))
    _add(Metric(f"{_g}_purchases", "Purchases", _band, "int", (lambda b, v, g=_g: b[f"mg_{g}_purchases"]), "Meta-reported purchases", "line", True, _name, ("meta",)))
    _add(Metric(f"{_g}_value", "Purchase value", _band, "money", (lambda b, v, g=_g: b[f"mg_{g}_value"]), "Meta-reported purchase value", "line", True, _name, ("meta",)))
    _add(Metric(f"{_g}_cvr", "CVR", _band, "pct", (lambda b, v, g=_g: div(b[f"mg_{g}_purchases"], b[f"mg_{g}_clicks"])), "Purchases / link clicks", "ratio", False, _name, ("meta",)))
    _add(Metric(f"{_g}_aov", "AOV", _band, "money2", (lambda b, v, g=_g: div(b[f"mg_{g}_value"], b[f"mg_{g}_purchases"])), "Purchase value / purchases", "ratio", False, _name, ("meta",)))
    _add(Metric(f"{_g}_cpa", "CPA", _band, "money2", (lambda b, v, g=_g: div(b[f"mg_{g}_spend"], b[f"mg_{g}_purchases"])), "Spend / purchases", "ratio", False, _name, ("meta",)))
    _add(Metric(f"{_g}_roas", "ROAS", _band, "x", (lambda b, v, g=_g: div(b[f"mg_{g}_value"], b[f"mg_{g}_spend"])), "Purchase value / spend", "ratio", False, _name, ("meta",)))


def metrics_for(view: str = "rollup", bands: list[str] | None = None) -> list[Metric]:
    out = [m for m in CATALOG if view in m.views]
    if bands:
        want = {b.lower() for b in bands}
        out = [m for m in out if m.band.lower() in want or m.band.lower().split(":")[0] in want]
    return out


def bands_for(view: str = "rollup") -> list[str]:
    seen: list[str] = []
    for m in CATALOG:
        if view in m.views and m.band not in seen:
            seen.append(m.band)
    return seen


def evaluate(b: dict, view: str = "rollup") -> dict:
    """All metrics of a view for one period's base measures (`b` behaves like a defaultdict(float); reach bases may be None).
    Plain and additive metrics are computed first, then the ratios in catalog order, so a ratio may use any plain metric and any ratio above it."""
    v: dict = {}
    todo = [m for m in CATALOG if view in m.views]
    for m in [m for m in todo if m.kind != "ratio"] + [m for m in todo if m.kind == "ratio"]:
        v[m.key] = m.fn(b, v)
    return v
