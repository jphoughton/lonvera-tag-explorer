"""Metric registry for the daily story: every daily metric the story can use, its basis, unit, formula and the analytics view and columns it comes from.
Discovered from information_schema and docs/DATA_SCHEMA.md (analytics schema), not guessed. The job upserts these rows into table daily_story_metrics each run;
analytics.daily_story_metric_registry reads them. `headline` is the one definition of the day's headline (Josh's rule): Upstack new-customer ROAS."""
from __future__ import annotations

# group, key, label, unit (usd | pct | ratio | count | x), basis, formula, source (analytics.view.column(s))
_R = [
    # ---- headline and its decomposition (basis: business's actual new customers over Meta + Google spend)
    ("headline", "nc_roas", "New-customer return on ad spend (NC ROAS)", "x", "Upstack new customers (actual orders)", "orders_daily.new_customer_revenue / (Meta spend + Google spend)", "analytics.orders_daily.new_customer_revenue; analytics.meta_ad_daily.spend; analytics.google_campaign_daily.spend"),
    ("headline", "aov", "New-customer average order value (AOV)", "usd", "Upstack new customers (actual orders)", "orders_daily.new_customer_revenue / orders_daily.new_customers", "analytics.orders_daily.new_customer_revenue, new_customers"),
    ("headline", "cpnc", "Cost per new customer", "usd", "Meta + Google spend over actual new customers", "(Meta spend + Google spend) / orders_daily.new_customers", "analytics.orders_daily.new_customers; analytics.meta_ad_daily.spend; analytics.google_campaign_daily.spend"),
    ("headline", "spend_total", "Total ad spend (Meta + Google)", "usd", "platform-reported dollars", "sum(Meta spend) + sum(Google spend)", "analytics.meta_ad_daily.spend; analytics.google_campaign_daily.spend"),
    ("headline", "nc_rev", "New-customer revenue", "usd", "Upstack new customers (actual orders), gross of tax and refunds", "orders_daily.new_customer_revenue", "analytics.orders_daily.new_customer_revenue"),
    ("headline", "nc", "New customers", "count", "Upstack new customers (actual orders)", "orders_daily.new_customers", "analytics.orders_daily.new_customers"),
    ("headline", "returning_rev", "Returning-customer revenue", "usd", "Upstack (actual orders), gross", "orders_daily.returning_customer_revenue", "analytics.orders_daily.returning_customer_revenue"),
    ("headline", "orders", "Orders (all)", "count", "Upstack (actual orders)", "orders_daily.orders", "analytics.orders_daily.orders"),
    # ---- Meta delivery and funnel
    ("meta", "spend_meta", "Meta spend", "usd", "Meta-reported dollars (both ad accounts)", "sum(meta_ad_daily.spend)", "analytics.meta_ad_daily.spend"),
    ("meta", "impressions", "Meta impressions", "count", "Meta-reported", "sum(meta_ad_daily.impressions)", "analytics.meta_ad_daily.impressions"),
    ("meta", "cpm", "Cost per 1,000 impressions (CPM)", "usd", "Meta-reported", "spend / impressions * 1000", "analytics.meta_ad_daily.spend, impressions"),
    ("meta", "link_clicks", "Meta link clicks", "count", "Meta-reported", "sum(meta_ad_daily.link_clicks)", "analytics.meta_ad_daily.link_clicks"),
    ("meta", "ctr", "Link click-through rate (CTR)", "pct", "Meta-reported", "link clicks / impressions", "analytics.meta_ad_daily.link_clicks, impressions"),
    ("meta", "cplc", "Cost per link click (CPLC)", "usd", "Meta-reported", "spend / link clicks", "analytics.meta_ad_daily.spend, link_clicks"),
    ("meta", "lpv", "Landing page views", "count", "Meta-reported", "sum(meta_ad_daily.landing_page_views)", "analytics.meta_ad_daily.landing_page_views"),
    ("meta", "atc", "Adds to cart", "count", "Meta-reported", "sum(meta_ad_daily.add_to_carts)", "analytics.meta_ad_daily.add_to_carts"),
    ("meta", "ic", "Initiated checkouts", "count", "Meta-reported (present only once the column exists in meta_ad_daily)", "sum(meta_ad_daily.<initiated checkout column>)", "analytics.meta_ad_daily (column discovered at run time)"),
    ("meta", "conv", "Conversion rate", "pct", "all new customers / Meta link clicks (Josh's definition)", "orders_daily.new_customers / sum(link_clicks)", "analytics.orders_daily.new_customers; analytics.meta_ad_daily.link_clicks"),
    ("meta", "c2lpv", "Funnel: link click to landing page view", "pct", "Meta-reported", "landing page views / link clicks", "analytics.meta_ad_daily.landing_page_views, link_clicks"),
    ("meta", "lpv2atc", "Funnel: landing page view to add to cart", "pct", "Meta-reported", "adds to cart / landing page views", "analytics.meta_ad_daily.add_to_carts, landing_page_views"),
    ("meta", "atc2ic", "Funnel: add to cart to initiated checkout", "pct", "Meta-reported (when the column exists)", "initiated checkouts / adds to cart", "analytics.meta_ad_daily (column discovered at run time)"),
    ("meta", "atc2nc", "Funnel: add to cart to new customer", "pct", "all new customers / Meta adds to cart (diluted by non-Meta customers)", "new customers / adds to cart", "analytics.orders_daily.new_customers; analytics.meta_ad_daily.add_to_carts"),
    ("meta", "ic2nc", "Funnel: initiated checkout to new customer", "pct", "all new customers / Meta initiated checkouts (when the column exists)", "new customers / initiated checkouts", "analytics.orders_daily.new_customers"),
    ("meta", "meta_roas", "Meta-reported ROAS (side data point)", "x", "Meta-reported (counts returning customers)", "sum(meta_revenue) / sum(spend)", "analytics.meta_ad_daily.meta_revenue, spend"),
    ("meta", "ad_roas", "Ad-attributed NC ROAS (a cut only, never the headline)", "x", "Upstack new customers attributed to Meta ads", "sum(upstack_new_customer_revenue) / sum(Meta spend)", "analytics.meta_ad_daily.upstack_new_customer_revenue, spend"),
    ("meta", "capped_share", "Share of Meta spend on capped ad sets", "pct", "Meta bid data (cost cap or ROAS floor)", "capped_spend / spend", "analytics.meta_bids_daily.capped_spend, spend"),
    ("meta", "floor_share", "Share of Meta spend on ROAS-floor ad sets", "pct", "Meta bid data", "floor_spend / spend", "analytics.meta_bids_daily.floor_spend, spend"),
    ("meta", "troas_scaling_share", "Share of scaling spend in target-ROAS (tROAS) campaigns", "pct", "campaign name parse (tROAS vs CostCap)", "scaling spend in campaigns named tROAS / scaling spend", "analytics.meta_campaign_daily.campaign_name, stage, spend"),
    ("meta", "d7_scaling_share", "Share of scaling spend in 7-day-click campaigns", "pct", "campaign name parse (7DC vs 1DC)", "scaling spend in campaigns named 7DC / scaling spend", "analytics.meta_campaign_daily.campaign_name, stage, spend"),
    ("meta", "testing_share", "Share of Meta spend in testing campaigns", "pct", "stage column", "testing spend / spend", "analytics.meta_campaign_daily.stage, spend"),
    # ---- Google
    ("google", "spend_google", "Google spend", "usd", "Google Ads (Upstack channel feed)", "sum(google_campaign_daily.spend)", "analytics.google_campaign_daily.spend"),
    ("google", "google_clicks", "Google clicks", "count", "Google Ads", "sum(google_campaign_daily.clicks)", "analytics.google_campaign_daily.clicks"),
    ("google", "google_nc", "Google-attributed new customers", "count", "Upstack any-click attribution", "sum(upstack_new_customers)", "analytics.google_campaign_daily.upstack_new_customers"),
    ("google", "google_nc_rev", "Google-attributed new-customer revenue", "usd", "Upstack any-click attribution", "sum(upstack_new_customer_revenue)", "analytics.google_campaign_daily.upstack_new_customer_revenue"),
    # ---- orders mix (Shopify)
    ("orders", "pack_1", "New-customer orders with 1 bottle", "pct", "Shopify new-customer orders, rebills, Trybe gifts, cancelled and test orders excluded", "share of qualifying orders by total bottles", "analytics.shopify_orders; analytics.shopify_order_lines.quantity"),
    ("orders", "pack_2", "New-customer orders with 2 bottles", "pct", "same exclusions", "share of qualifying orders", "analytics.shopify_order_lines.quantity"),
    ("orders", "pack_3", "New-customer orders with 3 bottles", "pct", "same exclusions", "share of qualifying orders", "analytics.shopify_order_lines.quantity"),
    ("orders", "pack_4p", "New-customer orders with 4 or more bottles", "pct", "same exclusions", "share of qualifying orders", "analytics.shopify_order_lines.quantity"),
    ("orders", "discount_share", "New-customer orders using a discount code", "pct", "same exclusions", "orders with a non-empty discount code / qualifying orders", "analytics.shopify_orders.discount_codes"),
    ("orders", "rebill_orders", "Rebill orders (excluded above)", "count", "Shopify", "count(is_rebill)", "analytics.shopify_orders.is_rebill"),
    ("orders", "refund_share", "Refunded share of new-customer revenue", "pct", "Shopify new-customer orders", "sum(refunded_amount) / sum(total_price)", "analytics.shopify_orders.refunded_amount, total_price"),
    # ---- email
    ("klaviyo", "email_sends", "Email and text sends (campaigns + flows)", "count", "Klaviyo", "sum(sends)", "analytics.klaviyo_daily.sends"),
    ("klaviyo", "campaign_sends", "Campaign sends", "count", "Klaviyo (source = campaign)", "sum(sends) where source = campaign", "analytics.klaviyo_daily.sends, source"),
    ("klaviyo", "flow_sends", "Flow sends", "count", "Klaviyo (source = flow)", "sum(sends) where source = flow", "analytics.klaviyo_daily.sends, source"),
    ("klaviyo", "klaviyo_rev", "Klaviyo-attributed revenue", "usd", "Klaviyo attribution of 'Placed Order - Upstackified' (not additive with ad revenue)", "sum(revenue)", "analytics.klaviyo_daily.revenue"),
    ("klaviyo", "campaign_rev", "Campaign-attributed revenue", "usd", "Klaviyo", "sum(revenue) where source = campaign", "analytics.klaviyo_daily.revenue"),
    ("klaviyo", "flow_rev", "Flow-attributed revenue", "usd", "Klaviyo", "sum(revenue) where source = flow", "analytics.klaviyo_daily.revenue"),
    # ---- Amazon
    ("amazon", "amazon_rev", "Amazon revenue", "usd", "ExpandFi Amazon feed", "amazon_daily.revenue", "analytics.amazon_daily.revenue"),
    ("amazon", "amazon_nc_rev", "Amazon new-customer revenue", "usd", "ExpandFi Amazon feed", "amazon_daily.new_customer_revenue", "analytics.amazon_daily.new_customer_revenue"),
    ("amazon", "amazon_ad_spend", "Amazon ad spend", "usd", "ExpandFi Amazon feed", "amazon_daily.ad_spend", "analytics.amazon_daily.ad_spend"),
    # ---- creative supply
    ("creative", "families_live", "Ad families with spend", "count", "ad families by first day of Meta spend (meta_ad_daily joined to meta_ads), count(distinct family)", "count(distinct family) with spend > 0", "analytics.meta_ad_daily; analytics.meta_ads.family_key, tracker_family_key"),
    ("creative", "families_launched", "Ad families launched (first day of spend)", "count", "first day of Meta spend, NOT the tracker launch_date", "count(distinct family) whose first spend day is the day", "analytics.meta_ad_daily; analytics.meta_ads"),
    ("creative", "new_adsets", "New ad sets (first day of spend)", "count", "meta_adset_daily first day", "count(distinct adset_id) whose first spend day is the day", "analytics.meta_adset_daily.adset_id, day"),
    ("creative", "new_ad_share", "Share of Meta spend on ads aged 7 days or less", "pct", "family first spend day", "spend of families first seen within 7 days / spend", "analytics.meta_ad_daily; analytics.meta_ads"),
    ("creative", "stage_moves", "Ad families moved to scaling that day", "count", "analytics.creative_stage_moves.scaling_first_day (definition in docs/TEST_TO_SCALE.md)", "count(family) with scaling_first_day = day and moved", "analytics.creative_stage_moves.scaling_first_day, moved"),
    # ---- site and placement
    ("site", "new_visitors", "New site visitors", "count", "Upstack site traffic", "site_traffic_daily.new_visitors", "analytics.site_traffic_daily.new_visitors"),
    ("site", "lp_changes", "Landing page content changes", "count", "analytics.landing_page_versions content_hash changes (history from 2026-11-11 only)", "versions whose content_hash differs from the page's previous version", "analytics.landing_page_versions.content_hash, fetched_at"),
    ("placement", "fb_share", "Share of Meta spend on Facebook placements", "pct", "meta_breakdown_daily publisher_platform (from 2026-10-12)", "facebook spend / all publisher_platform spend", "analytics.meta_breakdown_daily.publisher_platform"),
    ("placement", "ig_share", "Share of Meta spend on Instagram placements", "pct", "meta_breakdown_daily publisher_platform (from 2026-10-12)", "instagram spend / all", "analytics.meta_breakdown_daily.publisher_platform"),
]

REGISTRY = [dict(group=g, key=k, label=l, unit=u, basis=b, formula=f, source=s) for g, k, l, u, b, f, s in _R]
BY_KEY = {r["key"]: r for r in REGISTRY}
HEADLINE = "nc_roas"


def upsert(conn) -> int:
    """Write the registry into table daily_story_metrics (replace by key)."""
    with conn.cursor() as cur:
        for i, r in enumerate(REGISTRY):
            cur.execute("""INSERT INTO daily_story_metrics (metric_key, sort_order, metric_group, label, unit, basis, formula, source)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (metric_key) DO UPDATE SET sort_order=EXCLUDED.sort_order, metric_group=EXCLUDED.metric_group, label=EXCLUDED.label,
                             unit=EXCLUDED.unit, basis=EXCLUDED.basis, formula=EXCLUDED.formula, source=EXCLUDED.source""",
                        (r["key"], i, r["group"], r["label"], r["unit"], r["basis"], r["formula"], r["source"]))
    return len(REGISTRY)
