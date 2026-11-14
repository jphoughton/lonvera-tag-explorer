"""Curated data dictionary for the Lonvera marketing database: the ONE source file behind docs/DATA_SCHEMA.md, docs/schema.json and the
COMMENT ON statements in the migrations.

How it is used
  * scripts/gen_schema_doc.py reads the live column list / types / keys from Postgres, merges them with the text below and writes the docs.
  * tests/api/test_schema_docs.py FAILS when a table, view or column that exists in the `analytics` schema or in the public schema has no entry here
    (a new table must be described or listed in EXCLUDED with a reason), and when an entry here names something that no longer exists.
  * A description is plain text (markdown allowed in purpose / caveats). Keep it TRUE: say where the number comes from and what it is not.

Structure
  REL[key]   key = "schema.name". Fields: group, kind ('table' | 'view'), purpose, grain, source, refresh, coverage, caveats [..], cols {col: text},
             examples [(title, sql)], like (inherit cols from another key; cols given here win), pk (override of the introspected key), audience.
  EXCLUDED   public relations that are deliberately undocumented column by column, with the reason.
  The prose sections (TLDR, rules, join map, gaps, exclusions) are the *_MD constants at the end of the file.
"""
from __future__ import annotations

GROUPS = [
    ("analytics", "analytics schema: the read-only views (start here)"),
    ("meta", "Meta (Facebook / Instagram) ads: ads, campaigns, daily results"),
    ("upstack", "Upstack attribution of Meta orders (the Meta | Upstack switch)"),
    ("google", "Google Ads (campaign grain, from Upstack)"),
    ("orders", "Actual orders: the business's real new-customer revenue"),
    ("creative", "Creative tracker, tags, hooks, content"),
    ("rollups", "Derived creative-family roll-ups (rebuilt in full by etl.build_metrics)"),
    ("ops", "Pipeline bookkeeping"),
    ("proj_feeds", "Projection pacing feeds (daily actuals read by the Projections module; sheet-fed today, direct feeds in shadow)"),
    ("proj_engine", "Projection engine tables (finance, inventory, cohorts; read them through the analytics views)"),
    ("amazon", "Amazon data layer (ExpandFi merchant 1452): the tables behind the analytics.amazon_* views"),
    ("story", "Daily story: facts, the short explanation, the change patterns and the scoreboard"),
]

# Public relations that are not described column by column. Anything else in the public schema MUST have an entry in REL.
EXCLUDED = {
    "public.schema_migrations": "Migration runner bookkeeping (version, applied_at). Infrastructure, no business data.",
    "public.feed_oauth": "Holds the encrypted ExpandFi OAuth refresh token (a credential). Never documented column by column and never exposed.",
}

REL: dict[str, dict] = {}


def rel(key, **spec):
    assert key not in REL, key
    REL[key] = spec


# =====================================================================================================================================
# analytics: the read-only views
# =====================================================================================================================================

LAST_META_DAY = "(SELECT max(last_day) FROM analytics.data_coverage WHERE source = 'meta_ad_daily')"

rel("analytics.meta_ads", group="analytics", kind="view",
    purpose="Dimension: one row per Meta ad (every ad the pipeline has ever seen: 42K, most ARCHIVED or DELETED) with its campaign, ad set, product, status "
            "and all-time totals. Join to the daily views on ad_id (they already carry the main names).",
    grain="one row per Meta ad_id", source="public.meta_ads + meta_campaigns + meta_family_map + the product rule below + meta_ad_lifetime",
    refresh="nightly (etl service 13:00 UTC): ad names, campaigns and totals from the Meta Graph API, status refreshed every night",
    coverage="every ad with delivery since 2026-05-06 plus every ad the all-time pull returned; first_day / last_day only for ads that delivered since 2026-05-06",
    caveats=[
        "product_slug follows the Products table: a Meta family that maps to a tracker family counts under the product of that family's FIRST tracker row; a family "
        "off the tracker keeps the product guessed from its name, then campaign, then ad set; NULL = no active product. About 12K Meta families are on no tracker row.",
        "family_key is the lower-cased ad name with ' - Copy N' and merge-tag suffixes stripped: copies of one ad share a family.",
        "lifetime_* are Meta-reported all-time totals through the last complete day (Graph insights, both accounts, replaced nightly). They cover the whole life of the "
        "ad, not just 2026-05-06 onward; the daily views start on 2026-05-06.",
        "status is Meta's effective_status; CAMPAIGN_PAUSED and ADSET_PAUSED mean the ad itself is on but a parent is off.",
    ],
    cols={
        "ad_id": "Meta ad id (text, as Meta returns it).",
        "account": "Ad account name: 'Lonvera Main' (America/Los_Angeles) or 'Lonvera BackUp' (America/New_York).",
        "ad_name": "Current ad name in Meta (renames move the whole history).",
        "family_name": "Ad name without ' - Copy N' / merge-tag suffixes, original case: the ad family.",
        "family_key": "Lower-cased family_name. The join key to the creative tracker side (meta_family_map).",
        "tracker_family_key": "Creative-tracker family this Meta family was matched to by the resolver (NULL = no tracker row). Joins creative_tracker.family_key.",
        "product_slug": "vita-bull | beef-liver | boneflex | vita-belle | vital-flow | unassigned, by the rule in the view description; NULL = no active product.",
        "product_name": "Display name of product_slug.",
        "campaign_id": "Meta campaign id.",
        "campaign_name": "Campaign name. A name containing 'test' makes the campaign a testing campaign.",
        "stage": "'testing' when the campaign name contains 'test' (case-insensitive), else 'scaling'. A naming rule, not a Meta field.",
        "adset_id": "Meta ad set id.",
        "adset_name": "Ad set name.",
        "status": "Meta effective_status: ACTIVE, PAUSED, CAMPAIGN_PAUSED, ADSET_PAUSED, ARCHIVED, DELETED, DISAPPROVED, WITH_ISSUES.",
        "is_active": "status = 'ACTIVE' (the ad is eligible to deliver; it may still have spent nothing yet).",
        "created_date": "Pacific calendar date the ad was created.",
        "first_day": "First day with an ad_daily row (>= 2026-05-06); NULL for ads that never delivered in that window.",
        "last_day": "Last day with an ad_daily row.",
        "lifetime_spend": "All-time spend, dollars (Meta-reported). NULL when the ad has no lifetime row.",
        "lifetime_impressions": "All-time impressions.",
        "lifetime_link_clicks": "All-time link clicks (Meta 'link_click').",
        "lifetime_purchases": "All-time Meta-reported purchases.",
        "lifetime_revenue": "All-time Meta-reported purchase conversion value, dollars.",
    },
    examples=[
        ("Active ads and their spend, by product",
         "SELECT product_slug, count(*) AS active_ads, round(sum(lifetime_spend)) AS lifetime_spend\nFROM analytics.meta_ads\nWHERE is_active\nGROUP BY 1 ORDER BY 2 DESC;"),
        ("Ads that delivered in the last 7 days but are not active now",
         "SELECT ad_id, ad_name, status, last_day\nFROM analytics.meta_ads\nWHERE last_day >= " + LAST_META_DAY + " - 6 AND NOT is_active\nORDER BY last_day DESC LIMIT 50;"),
    ])

rel("analytics.meta_ad_daily", group="analytics", kind="view",
    purpose="The main Meta fact view: one row per Meta ad per day with Meta-reported results AND Upstack-attributed new customers / orders side by side, "
            "plus the ad's names, campaign, ad set, stage and product. Roll up from here; never add purchases to Google's.",
    grain="one row per (day, ad_id)",
    source="public.ad_daily (Meta Graph API, ad x day) combined per ad and day with public.ad_daily_upstack (Upstack any-click 30-day attribution mapped to ads), names from analytics.meta_ads",
    refresh="nightly (etl service 13:00 UTC): Meta trailing 28 days re-pulled and replaced (Meta restates conversions for weeks), Upstack trailing 7 days re-pulled",
    coverage="2026-05-06 through yesterday (Pacific). Meta days are each ad account's own calendar day; Upstack days are Pacific.",
    caveats=[
        "Three bases live in this view. spend, impressions, link_clicks, meta_purchases, meta_revenue are Meta's. upstack_new_customers / upstack_new_customer_revenue "
        "are Upstack's first-order basis (the 'NC' numbers: NC ROAS = upstack_new_customer_revenue / spend). upstack_orders / upstack_order_revenue are every order Upstack "
        "credits to the ad. Spend is Meta's in all three.",
        "upstack_* is NULL (not 0) on days Upstack has not been pulled; coverage starts 2026-05-06 as well.",
        "Upstack credits an order to every ad clicked in the 30 days before it (any-click), so Upstack-only ad-days exist where the ad spent nothing that day "
        "(spend = 0, meta_* = 0, upstack_* > 0). Filter spend > 0 only if you want delivery days.",
        "When one ad is copied into several ad sets under the same name, Upstack (which sees names, not ids) credit is split across the copies by their Meta spend that "
        "day. Family-level and higher sums are exact; one copy's own upstack_* numbers are an allocation.",
        "About 1.3% of Upstack-credited orders and 1.3% of new customers match no Meta ad ('(not set)', deleted ads); they are in orders_daily but in no row here.",
        "Meta purchases count returning customers; Upstack new customers do not. They are different quantities, not the same number measured twice.",
        "link_clicks is Meta's 'link_click' action count (link clicks, not all clicks). Impressions are Meta's.",
        "source: 'meta_api' for Graph-pulled rows, 'sheet' for the old family-grain backfill (none left in production), NULL on Upstack-only ad-days.",
    ],
    cols={
        "day": "Calendar day. Meta numbers: the ad account's own day (Main = America/Los_Angeles, BackUp = America/New_York, not re-based). Upstack numbers: Pacific.",
        "ad_id": "Meta ad id.",
        "ad_name": "Current ad name.",
        "product_slug": "Product the ad counts under (see analytics.meta_ads); NULL = no active product.",
        "campaign_id": "Meta campaign id (NULL on family-grain legacy rows).",
        "campaign_name": "Campaign name.",
        "stage": "'testing' | 'scaling' from the campaign name ('test' in the name = testing).",
        "adset_id": "Meta ad set id (NULL on family-grain legacy rows).",
        "adset_name": "Ad set name.",
        "account": "'Lonvera Main' or 'Lonvera BackUp'.",
        "source": "Where the Meta side of the row came from: 'meta_api' | 'sheet'; NULL when the row exists only because Upstack credited the ad.",
        "spend": "Meta spend, dollars (0 on Upstack-only ad-days). Additive across ads, ad sets, campaigns and across platforms.",
        "impressions": "Meta impressions.",
        "link_clicks": "Meta link clicks.",
        "meta_purchases": "Purchases Meta reports for the ad that day (omni_purchase, else purchase, else pixel purchase), Meta's default attribution; includes returning customers.",
        "meta_revenue": "Purchase conversion value Meta reports, dollars. meta_revenue / spend = Meta ROAS.",
        "upstack_new_customers": "First-time customers Upstack credits to the ad (any-click, 30-day window, utm_source 'Meta Ads'). NULL on days not pulled.",
        "upstack_new_customer_revenue": "Order total (tax included, before refunds) of those first orders, dollars. / spend = NC ROAS.",
        "upstack_orders": "All orders (new and returning customers) Upstack credits to the ad. NULL on days not pulled.",
        "upstack_order_revenue": "Order total of those orders, dollars (tax included, before refunds).",
        "landing_page_views": "Meta landing page views (omni_landing_page_view, else landing_page_view). NULL when the day has no Meta pull of it yet; 0 on Upstack-only ad-days.",
        "add_to_carts": "Meta adds to cart (omni_add_to_cart, else add_to_cart, else pixel add to cart), the 'Adds to cart' of Ads Manager; includes returning customers. NULL when not pulled yet; 0 on Upstack-only ad-days.",
        "initiated_checkouts": "Meta initiated checkouts (omni_initiated_checkout, else initiate_checkout, else pixel initiate checkout), the 'Checkouts initiated' of Ads Manager; includes returning customers. NULL when not pulled yet; 0 on Upstack-only ad-days.",
        "payment_info_adds": "Meta payment info added (omni_add_payment_info, else add_payment_info, else pixel add payment info), the checkout step between initiated checkout and purchase. NULL when not pulled yet; 0 on Upstack-only ad-days.",
    },
    examples=[
        ("Ad level: 30-day spend and Meta-reported ROAS",
         "SELECT ad_id, ad_name, product_slug,\n       round(sum(spend), 2) AS spend,\n       sum(meta_purchases) AS purchases,\n       round(sum(meta_revenue) / NULLIF(sum(spend), 0), 2) AS roas\n"
         "FROM analytics.meta_ad_daily\nWHERE day > " + LAST_META_DAY + " - 30\nGROUP BY 1, 2, 3\nHAVING sum(spend) > 0\nORDER BY spend DESC LIMIT 25;"),
        ("Ad level: new-customer ROAS and cost per new customer on the Upstack basis (30 days)",
         "SELECT ad_id, ad_name,\n       round(sum(spend), 2) AS spend,\n       sum(upstack_new_customers) AS new_customers,\n"
         "       round(sum(spend) / NULLIF(sum(upstack_new_customers), 0), 2) AS nc_cpa,\n       round(sum(upstack_new_customer_revenue) / NULLIF(sum(spend), 0), 2) AS nc_roas\n"
         "FROM analytics.meta_ad_daily\nWHERE day > " + LAST_META_DAY + " - 30\nGROUP BY 1, 2\nHAVING sum(spend) > 0\nORDER BY spend DESC LIMIT 25;"),
        ("Spend by product by day (last 14 days)",
         "SELECT day, coalesce(product_slug, '(no product)') AS product, round(sum(spend), 2) AS spend\nFROM analytics.meta_ad_daily\n"
         "WHERE day > " + LAST_META_DAY + " - 14\nGROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC;"),
        ("Funnel by product, last 7 days: link click to add to cart, landing page view to add to cart, add to cart to purchase",
         "SELECT coalesce(product_slug, '(no product)') AS product, sum(link_clicks) AS link_clicks, sum(landing_page_views) AS landing_page_views, sum(add_to_carts) AS add_to_carts,\n"
         "       sum(meta_purchases) AS purchases,\n       round(sum(add_to_carts) / NULLIF(sum(link_clicks), 0), 4) AS link_click_to_atc,\n"
         "       round(sum(add_to_carts) / NULLIF(sum(landing_page_views), 0), 4) AS lpv_to_atc,\n       round(sum(meta_purchases) / NULLIF(sum(add_to_carts), 0), 4) AS atc_to_purchase\n"
         "FROM analytics.meta_ad_daily\nWHERE day > " + LAST_META_DAY + " - 7 AND add_to_carts IS NOT NULL\nGROUP BY 1 ORDER BY sum(spend) DESC;"),
        ("Checkout funnel by product, last 7 days: add to cart to initiated checkout, initiated checkout to purchase",
         "SELECT coalesce(product_slug, '(no product)') AS product, sum(add_to_carts) AS add_to_carts, sum(initiated_checkouts) AS initiated_checkouts, sum(payment_info_adds) AS payment_info_adds,\n"
         "       sum(meta_purchases) AS purchases,\n       round(sum(initiated_checkouts) / NULLIF(sum(add_to_carts), 0), 4) AS atc_to_ic,\n"
         "       round(sum(meta_purchases) / NULLIF(sum(initiated_checkouts), 0), 4) AS ic_to_purchase\n"
         "FROM analytics.meta_ad_daily\nWHERE day > " + LAST_META_DAY + " - 7 AND initiated_checkouts IS NOT NULL\nGROUP BY 1 ORDER BY sum(spend) DESC;"),
    ])

rel("analytics.meta_adset_daily", group="analytics", kind="view",
    purpose="Ad set x day roll-up of meta_ad_daily (Meta-reported and Upstack bases side by side).",
    grain="one row per (day, adset_id)", source="analytics.meta_ad_daily grouped by day and ad set",
    refresh="same as meta_ad_daily", coverage="2026-05-06 through yesterday",
    caveats=["Same bases and NULL rules as meta_ad_daily. adset_id is NULL only for legacy family-grain rows (none in production).",
             "An ad set belongs to one campaign; names are the current ones. There is no product column because an ad set can hold ads of several products: "
             "group meta_ad_daily by product_slug instead."],
    cols={
        "day": "Calendar day (see meta_ad_daily).",
        "adset_id": "Meta ad set id.",
        "adset_name": "Ad set name.",
        "campaign_id": "Parent campaign id.",
        "campaign_name": "Parent campaign name.",
        "stage": "'testing' | 'scaling' from the campaign name.",
        "account": "'Lonvera Main' or 'Lonvera BackUp'.",
        "ads_with_spend": "Distinct ads in the ad set with spend > 0 that day.",
        "spend": "Meta spend, dollars.",
        "impressions": "Meta impressions.",
        "link_clicks": "Meta link clicks.",
        "meta_purchases": "Meta-reported purchases.",
        "meta_revenue": "Meta-reported purchase value, dollars.",
        "upstack_new_customers": "Upstack first-time customers credited to the ad set's ads (NULL on days not pulled).",
        "upstack_new_customer_revenue": "Their first-order revenue, dollars.",
        "upstack_orders": "All Upstack-credited orders.",
        "upstack_order_revenue": "Revenue of those orders, dollars.",
    },
    examples=[
        ("Daily spend and Meta ROAS of one campaign's ad sets",
         "SELECT day, adset_name, round(spend, 2) AS spend, meta_purchases,\n       round(meta_revenue / NULLIF(spend, 0), 2) AS roas\nFROM analytics.meta_adset_daily\n"
         "WHERE campaign_id = (SELECT campaign_id FROM analytics.meta_campaign_daily ORDER BY day DESC, spend DESC LIMIT 1)\n  AND day > " + LAST_META_DAY + " - 7\nORDER BY day DESC, spend DESC;"),
        ("Ad sets ranked by 7-day new-customer CPA (at least $200 spent)",
         "SELECT adset_id, max(adset_name) AS adset_name, round(sum(spend), 2) AS spend, sum(upstack_new_customers) AS new_customers,\n"
         "       round(sum(spend) / NULLIF(sum(upstack_new_customers), 0), 2) AS nc_cpa\nFROM analytics.meta_adset_daily\nWHERE day > " + LAST_META_DAY + " - 7\n"
         "GROUP BY 1 HAVING sum(spend) >= 200 ORDER BY nc_cpa NULLS LAST LIMIT 25;"),
    ])

rel("analytics.meta_campaign_daily", group="analytics", kind="view",
    purpose="Campaign x day roll-up of meta_ad_daily (Meta-reported and Upstack bases side by side).",
    grain="one row per (day, campaign_id)", source="analytics.meta_ad_daily grouped by day and campaign",
    refresh="same as meta_ad_daily", coverage="2026-05-06 through yesterday (use meta_campaign_daily_history for campaign-level days back to 2024-12-31)",
    caveats=["Same bases and NULL rules as meta_ad_daily.", "A campaign can run ads of several products (shared All_US / test campaigns): split by product with meta_ad_daily."],
    cols={
        "day": "Calendar day (see meta_ad_daily).",
        "campaign_id": "Meta campaign id.",
        "campaign_name": "Campaign name.",
        "stage": "'testing' | 'scaling' from the campaign name.",
        "account": "'Lonvera Main' or 'Lonvera BackUp'.",
        "adsets_with_spend": "Distinct ad sets with spend > 0 that day.",
        "ads_with_spend": "Distinct ads with spend > 0 that day.",
        "spend": "Meta spend, dollars.",
        "impressions": "Meta impressions.",
        "link_clicks": "Meta link clicks.",
        "meta_purchases": "Meta-reported purchases.",
        "meta_revenue": "Meta-reported purchase value, dollars.",
        "upstack_new_customers": "Upstack first-time customers credited to the campaign's ads (NULL on days not pulled).",
        "upstack_new_customer_revenue": "Their first-order revenue, dollars.",
        "upstack_orders": "All Upstack-credited orders.",
        "upstack_order_revenue": "Revenue of those orders, dollars.",
    },
    examples=[
        ("Campaigns, last 7 days: spend, Meta ROAS, NC ROAS",
         "SELECT campaign_id, max(campaign_name) AS campaign, max(stage) AS stage, round(sum(spend)) AS spend,\n       round(sum(meta_revenue) / NULLIF(sum(spend), 0), 2) AS meta_roas,\n"
         "       round(sum(upstack_new_customer_revenue) / NULLIF(sum(spend), 0), 2) AS nc_roas\nFROM analytics.meta_campaign_daily\nWHERE day > " + LAST_META_DAY + " - 7\n"
         "GROUP BY 1 HAVING sum(spend) > 0 ORDER BY sum(spend) DESC LIMIT 25;"),
        ("Daily spend, testing vs scaling",
         "SELECT day, stage, round(sum(spend), 2) AS spend\nFROM analytics.meta_campaign_daily\nWHERE day > " + LAST_META_DAY + " - 30\nGROUP BY 1, 2 ORDER BY 1 DESC, 2;"),
    ])

rel("analytics.meta_campaign_daily_history", group="analytics", kind="view",
    purpose="Meta campaign x day back to 2024-12-31: the only Meta data before 2026-05-06 (campaign grain, no ad sets or ads). From 2026-05-06 on it equals "
            "meta_campaign_daily's Meta-reported columns.",
    grain="one row per (day, campaign_id) after the day rule (a day is read from 'app' when it has app rows, else from 'sheet')",
    source="public.proj_feed_meta_day: 'app' = aggregated from ad_daily by the app, 'sheet' = the Lonvera Projections sheet tab 'Meta Campaign - History'",
    refresh="nightly (etl service 13:00 UTC via etl.projection_daily); sheet rows never change after the backfill",
    coverage="2024-12-31 through yesterday (Pacific)",
    caveats=["Meta-reported only (no Upstack columns). product and stage are classified from the CAMPAIGN NAME here ('VitaBull', 'Beef Liver', 'Boneflex', "
             "'Unclassified'; 'Testing' / 'Scaling'), not through the tracker, so they can differ from analytics.meta_ads.product_slug.",
             "Use it for trends across the May 2026 boundary; the ad-grain views cannot go back that far."],
    cols={
        "day": "Calendar day.",
        "campaign_id": "Meta campaign id.",
        "campaign_name": "Campaign name.",
        "product": "Product guessed from the campaign name: VitaBull | Beef Liver | Boneflex | Unclassified.",
        "stage": "'Testing' when the campaign name contains 'test', else 'Scaling' (capitalised here).",
        "source": "'app' (aggregated from ad_daily) or 'sheet' (the sheet's campaign history).",
        "spend": "Meta spend, dollars.",
        "impressions": "Meta impressions.",
        "link_clicks": "Meta link clicks.",
        "meta_purchases": "Meta-reported purchases.",
        "meta_revenue": "Meta-reported purchase value, dollars.",
    },
    examples=[
        ("Monthly Meta spend by product since 2025",
         "SELECT date_trunc('month', day)::date AS month, product, round(sum(spend)) AS spend\nFROM analytics.meta_campaign_daily_history\nWHERE day >= '2025-01-01'\nGROUP BY 1, 2 ORDER BY 1, 3 DESC;"),
        ("Meta ROAS by month (Meta-reported)",
         "SELECT date_trunc('month', day)::date AS month, round(sum(spend)) AS spend, round(sum(meta_revenue) / NULLIF(sum(spend), 0), 2) AS roas\n"
         "FROM analytics.meta_campaign_daily_history GROUP BY 1 ORDER BY 1;"),
    ])

rel("analytics.meta_reach", group="analytics", kind="view",
    purpose="Account-level reach and frequency for Meta (both ad accounts summed). Reach is de-duplicated by Meta per period, so it is stored at each grain and "
            "must never be summed across days or weeks.",
    grain="one row per (grain, period_key): grain is 'day', 'week' (ISO) or 'month'",
    source="public.proj_feed_meta_reach (sheet tab 'Meta Account - Reach' today; the feeds service writes a shadow copy in proj_feedx_meta_reach)",
    refresh="nightly (etl service via etl.projection_daily); the trailing days are replaced",
    coverage="day 2025-01-01.., week 2024-12-30.., month 2025-01-01..; the current day, week and month are partial",
    caveats=["Account level only: there is NO reach / frequency per ad, ad set, campaign or product (the product table, proj_feed_product_reach, holds unique outbound clicks only).",
             "The last day is today (partial). Compare complete periods only.",
             "frequency = impressions / reach, computed here (NULL when reach is 0)."],
    cols={
        "grain": "'day' | 'week' | 'month'.",
        "period_key": "'2026-11-02' (day) | '2026-W45' (ISO week) | '2026-11' (month).",
        "period_start": "First day of the period.",
        "period_end": "Last day of the period (the current period ends today and is partial).",
        "impressions": "Meta impressions over the period.",
        "reach": "People reached over the period (Meta de-duplicated; not additive across periods).",
        "frequency": "impressions / reach, 4 decimals.",
        "unique_link_clicks": "Unique link clicks over the period.",
        "link_clicks": "Link clicks over the period.",
        "unique_outbound_clicks": "Unique outbound clicks over the period (Meta de-duplicated).",
        "loaded_at": "When the row was loaded.",
    },
    examples=[
        ("Weekly reach and frequency, last 12 complete weeks",
         "SELECT period_key, period_start, impressions, reach, frequency\nFROM analytics.meta_reach\nWHERE grain = 'week' AND period_end < current_date\nORDER BY period_start DESC LIMIT 12;"),
        ("Monthly unique outbound clicks per 1,000 reached",
         "SELECT period_key, reach, unique_outbound_clicks, round(1000.0 * unique_outbound_clicks / NULLIF(reach, 0), 1) AS uoc_per_1000_reached\n"
         "FROM analytics.meta_reach WHERE grain = 'month' ORDER BY period_start DESC LIMIT 12;"),
    ])

rel("analytics.google_campaign_daily", group="analytics", kind="view",
    purpose="Google Ads campaign x day: Google's own spend, impressions, clicks and conversions plus Upstack's attributed new customers / orders. "
            "Google has no ad group, ad or keyword grain in this database.",
    grain="one row per (day, campaign_key)",
    source="public.google_daily (Upstack channel feed joined to Upstack any-click 30-day attribution by campaign name) + upstack_google_campaigns",
    refresh="nightly (etl service 13:00 UTC): trailing 7 days re-pulled; the job refuses a day with more than $200 spend and no attributed orders",
    coverage="Google's own numbers 2024-03-25 through yesterday (Pacific); Upstack columns from 2025-05-13 (NULL before)",
    caveats=["Two bases: google_* are Google's own conversions (restated by Google for days afterwards); upstack_* are Upstack's attribution of orders to the "
             "campaign (utm campaign name), any-click 30-day, which credits Google with orders that also had a Meta click.",
             "Purchases and revenue overlap with Meta's: never add google_* or upstack_* to Meta's. Spend, impressions and clicks are additive.",
             "campaign_type is read from the campaign NAME (Shopping, Search, Performance Max, Demand Gen, Other); Upstack exposes none. 'Other' is never guessed.",
             "in_feed = false rows are Upstack-attributed orders whose utm campaign matches no Google campaign ('(not set)', raw ids, deleted campaigns): spend 0, "
             "kept so totals tie to Upstack's Google total (about 3% of orders).",
             "clicks are all clicks (Google has no link-click event)."],
    cols={
        "day": "Pacific day (Upstack buckets by America/Los_Angeles).",
        "campaign_key": "Google campaign id, or 'name:<utm campaign>' when the utm value is no Google campaign (in_feed = false).",
        "campaign_name": "Campaign name.",
        "campaign_type": "Shopping | Search | Performance Max | Demand Gen | Other, read from the name.",
        "campaign_status": "Campaign status from the Upstack feed (NULL for in_feed = false rows).",
        "bid_strategy": "Bid strategy from the Upstack feed (NULL for in_feed = false rows).",
        "in_feed": "true = a Google campaign in the channel feed; false = utm campaign with no Google campaign.",
        "spend": "Google spend, dollars.",
        "impressions": "Google impressions.",
        "clicks": "Google clicks (all clicks).",
        "google_purchases": "Purchases Google reports (delivered through Upstack's ad-platform feed).",
        "google_revenue": "Conversion value Google reports, dollars.",
        "upstack_new_customers": "First-time customers Upstack credits to the campaign (any-click, 30 days, utm_source 'Google Ads'). NULL before 2025-05-13.",
        "upstack_new_customer_revenue": "Their first-order revenue, dollars (tax included, before refunds).",
        "upstack_orders": "All orders Upstack credits to the campaign.",
        "upstack_order_revenue": "Revenue of those orders, dollars.",
    },
    examples=[
        ("Spend and Google-reported ROAS by campaign type, last 30 days",
         "SELECT campaign_type, round(sum(spend)) AS spend, round(sum(google_revenue) / NULLIF(sum(spend), 0), 2) AS google_roas,\n"
         "       round(sum(upstack_new_customer_revenue) / NULLIF(sum(spend), 0), 2) AS nc_roas\nFROM analytics.google_campaign_daily\n"
         "WHERE day > (SELECT max(last_day) FROM analytics.data_coverage WHERE source = 'google_campaign_daily') - 30\nGROUP BY 1 ORDER BY 2 DESC;"),
        ("Top Google campaigns by spend this month",
         "SELECT campaign_name, campaign_type, round(sum(spend)) AS spend, sum(clicks) AS clicks\nFROM analytics.google_campaign_daily\n"
         "WHERE day >= date_trunc('month', current_date - 1)\nGROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;"),
    ])

rel("analytics.platform_daily", group="analytics", kind="view",
    purpose="Meta and Google side by side per day. Spend, impressions and clicks are additive across the two platforms; purchases and revenue are not.",
    grain="one row per (day, platform) with platform 'meta' or 'google'",
    source="analytics.meta_ad_daily and analytics.google_campaign_daily grouped by day",
    refresh="nightly (etl service 13:00 UTC)",
    coverage="Meta 2026-05-06.., Google 2024-03-25.. through yesterday (Pacific); Total NC ROAS is defined only on days both platforms and orders_daily have",
    caveats=["spend is additive: Total spend = sum over platforms. own_purchases / own_revenue / upstack_* are NOT: both platforms credit the same orders (the overlap "
             "cannot be measured), so adding them gives the wrong number. Never divide a sum of platform revenue by total spend.",
             "Total NC ROAS = actual new-customer revenue (analytics.orders_daily.new_customer_revenue, counted once) / total spend of the same days. Use the example below.",
             "clicks: Meta = link clicks, Google = all clicks. own_* is each platform's own reporting; upstack_* is Upstack's any-click 30-day attribution.",
             "Meta days are each ad account's own day, Google and Upstack days are Pacific; the one-day offset matters only for the BackUp account (New York)."],
    cols={
        "day": "Calendar day (Meta: ad account day; Google / Upstack: Pacific).",
        "platform": "'meta' | 'google'.",
        "spend": "Platform spend, dollars. Additive across platforms.",
        "impressions": "Platform impressions.",
        "clicks": "Meta: link clicks. Google: all clicks.",
        "own_purchases": "Purchases the platform reports itself. Not additive across platforms.",
        "own_revenue": "Conversion value the platform reports itself, dollars. Not additive across platforms.",
        "upstack_new_customers": "Upstack first-time customers credited to the platform (NULL on days Upstack was not pulled). Not additive across platforms.",
        "upstack_new_customer_revenue": "Their first-order revenue, dollars. Not additive across platforms.",
        "upstack_orders": "All orders Upstack credits to the platform. Not additive across platforms.",
        "upstack_order_revenue": "Revenue of those orders, dollars. Not additive across platforms.",
    },
    examples=[
        ("Meta vs Google spend per day (last 14 days)",
         "SELECT day,\n       round(sum(spend) FILTER (WHERE platform = 'meta'), 2) AS meta_spend,\n       round(sum(spend) FILTER (WHERE platform = 'google'), 2) AS google_spend,\n"
         "       round(sum(spend), 2) AS total_spend\nFROM analytics.platform_daily\nWHERE day > " + LAST_META_DAY + " - 14\nGROUP BY 1 ORDER BY 1 DESC;"),
        ("Total spend and Total NC ROAS by day (actual new-customer revenue / spend, days both platforms and orders cover)",
         "WITH s AS (\n  SELECT day, sum(spend) AS spend, count(*) AS platforms FROM analytics.platform_daily GROUP BY day\n)\n"
         "SELECT s.day, round(s.spend, 2) AS total_spend, o.new_customer_revenue,\n       round(o.new_customer_revenue / NULLIF(s.spend, 0), 4) AS total_nc_roas\n"
         "FROM s JOIN analytics.orders_daily o USING (day)\nWHERE s.platforms = 2 AND s.day > " + LAST_META_DAY + " - 30\nORDER BY s.day DESC;"),
        ("Total NC ROAS for a window (ratio of sums, never a mean of daily ratios)",
         "WITH s AS (SELECT day, sum(spend) AS spend, count(*) AS platforms FROM analytics.platform_daily GROUP BY day)\n"
         "SELECT count(*) AS days, round(sum(s.spend)) AS total_spend, round(sum(o.new_customer_revenue)) AS nc_revenue,\n       round(sum(o.new_customer_revenue) / NULLIF(sum(s.spend), 0), 4) AS total_nc_roas\n"
         "FROM s JOIN analytics.orders_daily o USING (day)\nWHERE s.platforms = 2 AND s.day > " + LAST_META_DAY + " - 30;"),
    ])

rel("analytics.orders_daily", group="analytics", kind="view",
    purpose="The business's ACTUAL revenue and customers per Pacific day, counted once (Upstack order-level measures). new_customer_revenue is the numerator of Total NC ROAS.",
    grain="one row per Pacific day with orders", source="public.upstack_orders_daily (Upstack /api/query, orders.* measures, America/Los_Angeles)",
    refresh="nightly (etl service 13:00 UTC): trailing 7 days replaced because Upstack restates recent days",
    coverage="2025-01-01 through yesterday (Pacific)",
    caveats=["Revenue is the order total as Upstack reports it (tax included), before refunds: all numbers are gross.",
             "There is no product, platform or campaign dimension: you cannot split new-customer revenue by product here. Use the attributed columns of the Meta / Google views "
             "(an attribution, not the actual) or analytics.orders_by_product_daily (the sheet's NC Rev - By Product: actual first-order revenue by the order's largest product line).",
             "A day with no orders is absent, not zero.",
             "new_customers is the count of first-time customers (first orders)."],
    cols={
        "day": "Pacific day.",
        "new_customer_revenue": "Revenue of first orders that day, dollars (tax included, before refunds). orders.new_customer_total_revenue.",
        "new_customers": "Number of new customers (first orders) that day. orders.new_customers.",
        "returning_customer_revenue": "Revenue of repeat orders, dollars (subscription rebills included). orders.returning_customer_total_revenue.",
        "total_revenue": "new + returning revenue, dollars. orders.total_revenue.",
        "orders": "All orders that day (new and returning). orders.count.",
        "loaded_at": "When the day was last (re)pulled from Upstack.",
    },
    examples=[
        ("New-customer revenue and new customers by week",
         "SELECT date_trunc('week', day)::date AS week, round(sum(new_customer_revenue)) AS nc_revenue, sum(new_customers) AS new_customers,\n"
         "       round(sum(new_customer_revenue) / NULLIF(sum(new_customers), 0), 2) AS nc_aov\nFROM analytics.orders_daily\nWHERE day >= current_date - 90\nGROUP BY 1 ORDER BY 1 DESC;"),
        ("New vs returning share of revenue, last 30 days",
         "SELECT round(sum(new_customer_revenue)) AS new_rev, round(sum(returning_customer_revenue)) AS returning_rev,\n"
         "       round(100 * sum(new_customer_revenue) / NULLIF(sum(total_revenue), 0), 1) AS new_pct\nFROM analytics.orders_daily\nWHERE day > (SELECT max(day) FROM analytics.orders_daily) - 30;"),
    ])

rel("analytics.creative_tracker", group="analytics", kind="view",
    purpose="One row per live Creative Tracker row (a concept / ad variant) with its current tag in the product's own tag set, its hook and its since-launch "
            "Meta-reported metrics. The way to ask 'which cluster / ICP / pain point / hook performs'.",
    grain="one row per tracker row (tracker_row_id); an 'ad family' (family_key) can span several rows",
    source="public.tracker_rows + ad_tags (current) + taxonomy_lines + ad_hooks + ad_metrics + products",
    refresh="tracker rows synced nightly from the Creative Key Google Sheet (etl service 13:00 UTC); linemap tags and hooks are loaded by hand per tagging cycle (versions dated 2026-11-03 to 2026-11-05)",
    coverage="all live tracker rows (~3,980); soft-deleted rows are left out",
    caveats=["Metrics (spend_launch ... ads_active_count) sit on the FIRST tracker row of each ad family only; the other rows of the family read 0 "
             "(is_first_row says which). So sum over rows is right and a per-row average is wrong. 'Unique ads' = count of distinct family_key.",
             "Since-launch metrics are Meta-reported (Graph API all-time per ad, summed over the family's Meta ads), not Upstack. spend_7d / _30d end on the last complete day.",
             "Tags: products.tag_set decides which set drives the hierarchy: 'linemap' (cluster / ICP / pain point / trigger of the Communication Overview line, "
             "read from the creative image or video by a tagger, with a confidence) for Vita Bull, Beef Liver and Boneflex; 'tracker' (the tracker's own columns) for the rest. "
             "The tracker_* columns are always the tracker's own tags. A row with no current tag has NULL tag columns.",
             "A family whose tracker rows carry different tags appears under each of them; its spend counts only where its first row sits.",
             "asset_url is a Google Drive link to the creative file; access to the file is governed by Drive, not by this database."],
    cols={
        "tracker_row_id": "Primary key (tracker_rows.id).",
        "row_key": "Stable row key: concept id + '#' + ordinal among rows sharing (concept id, ad family). Never a sheet row number.",
        "product_slug": "vita-bull | beef-liver | boneflex | vita-belle | vital-flow | unassigned, from the tracker's Product column.",
        "product_name": "Display name of the product.",
        "concept_id": "Tracker Concept ID (text: '0510' is not '510').",
        "ad_family": "Tracker 'Ad Family' as typed; empty when blank.",
        "family_key": "lower(ad_family), or 'id:' + lower(concept_id) when blank. The unique-ad key; joins analytics.meta_ads.tracker_family_key.",
        "status": "Tracker status, e.g. '8 - Launched', '5 - Creative In Progress', '3 - Briefing'.",
        "ad_type": "VID (video) | IMG (image) | NULL.",
        "style": "Creative style (tracker column).",
        "vendor": "Who made it (tracker column), e.g. Madre, Victoria, Internal, 10xROAS.",
        "fidelity": "Creative fidelity (tracker column).",
        "proof": "Proof mechanism (tracker column).",
        "launch_date": "Launch date from the tracker (a date typed by the team; NULL when blank).",
        "concept_name": "Concept name.",
        "description": "Short description.",
        "hypothesis": "Why the team tested it.",
        "meta_ad_name": "The ad name to look for in Meta (tracker 'Name' column).",
        "primary_text": "Ad primary texts (array, up to 3).",
        "headlines": "Ad headlines (array, up to 3).",
        "ad_description": "Ad description line.",
        "transcript": "Video transcript from the tracker (can differ from the audio actually running; the linemap taggers re-transcribed top spenders).",
        "asset_url": "Link to the creative asset (Google Drive).",
        "tracker_cluster": "Tracker's own Market Cluster.",
        "tracker_icp": "Tracker's own ICP (persona) column.",
        "tracker_pain": "Tracker's own Pain Point.",
        "tracker_emotion": "Tracker's own Emotion.",
        "tracker_ingredient": "Tracker's Lead Ingredient.",
        "tag_set": "Which tag set the tag columns come from: 'linemap' | 'tracker' (products.tag_set); NULL when the row is untagged in that set.",
        "tag_version": "Tagging cycle, e.g. 'linemap-v2-2026-11-03', 'tracker-live'.",
        "line_code": "Communication Overview line code (L01.. Vita Bull, B01.. Beef Liver, BF01.. Boneflex); NULL for tracker tags.",
        "cluster": "Market cluster of the tag.",
        "icp": "ICP / persona of the tag.",
        "pain_point": "Pain point of the tag.",
        "emotional_trigger": "Emotional trigger of the line (linemap tags).",
        "tag_confidence": "high | medium | low (linemap tags): how sure the tagger was; agreement of a blind re-tag is stored in app_meta reliability:<product>.",
        "tag_method": "How it was tagged: 'image', 'video: 4 frames + Whisper transcript', 'text only', 'no picture (copy only)', 'video: thumbnail + no audio' ...",
        "hook": "The ad's hook: first spoken line plus on-image headline, one short line (NULL when none was readable).",
        "hook_source": "spoken | on_screen | both | copy | none.",
        "is_first_row": "true on the one row of the family that carries its metrics.",
        "spend_launch": "Since-launch Meta spend of the family, dollars (first row only).",
        "purchases_launch": "Since-launch Meta-reported purchases (first row only).",
        "revenue_launch": "Since-launch Meta-reported purchase value, dollars (first row only).",
        "spend_7d": "Spend over the 7 days ending the last complete day (first row only).",
        "spend_30d": "Spend over the 30 days ending the last complete day (first row only).",
        "purchases_30d": "Meta-reported purchases over the same 30 days (first row only).",
        "revenue_30d": "Meta-reported purchase value over the same 30 days (first row only).",
        "live": "true when the family spent on either of the last two days (first row only).",
        "ads_active_count": "Meta ads of the family that are ACTIVE now (same value on every row of the family).",
    },
    examples=[
        ("Vita Bull since-launch spend and Meta ROAS by cluster / ICP / pain point (first rows only, so each family counts once)",
         "SELECT cluster, icp, pain_point, count(*) AS ads, round(sum(spend_launch)) AS spend,\n       round(sum(revenue_launch) / NULLIF(sum(spend_launch), 0), 2) AS roas\n"
         "FROM analytics.creative_tracker\nWHERE product_slug = 'vita-bull' AND is_first_row\nGROUP BY 1, 2, 3 ORDER BY sum(spend_launch) DESC NULLS LAST LIMIT 20;"),
        ("Top creative families of the last 30 days with their tags and hook",
         "SELECT family_key, product_slug, cluster, icp, pain_point, hook, round(spend_30d) AS spend_30d,\n       round(revenue_30d / NULLIF(spend_30d, 0), 2) AS roas_30d\n"
         "FROM analytics.creative_tracker\nWHERE is_first_row AND spend_30d > 0\nORDER BY spend_30d DESC LIMIT 20;"),
        ("Tag coverage by product",
         "SELECT product_slug, count(*) AS rows, count(*) FILTER (WHERE tag_set IS NOT NULL) AS tagged,\n       count(hook) AS with_hook\nFROM analytics.creative_tracker GROUP BY 1 ORDER BY 2 DESC;"),
    ])

rel("analytics.data_coverage", group="analytics", kind="view",
    purpose="Freshness and coverage per data source: first and last day held, days, rows, when last loaded. Read it before quoting 'yesterday' or a trailing window.",
    grain="one row per source", source="min / max of each source table's day column",
    refresh="live (computed on every query)", coverage="all sources",
    caveats=["last_day is the last day LOADED. For Meta, Upstack, Google and orders that is yesterday Pacific; today is never loaded. "
             "The meta_reach day grain ends today and the last row is partial.",
             "The last 3 to 7 days still move: Meta restates conversions for weeks (28 trailing days re-pulled nightly), Upstack and orders re-pull 7 days."],
    cols={
        "source": "meta_ad_daily | upstack_meta_attribution | google_campaign_daily | upstack_google_attribution | orders_daily | meta_reach (day grain) | meta_campaign_daily_history | amazon_ads_campaign_daily | amazon_ads_asin_daily | amazon_search_queries_weekly | amazon_search_queries_monthly | amazon_orders_asin_daily | amazon_traffic_daily | amazon_market_baskets | amazon_repeat_purchases | amazon_settlement_fees_daily.",
        "first_day": "First day held.",
        "last_day": "Last day held.",
        "days": "Distinct days held.",
        "rows": "Rows (or ad rows) held.",
        "loaded_at": "Latest load / pull time of the source.",
        "note": "Timezone and refresh note for the source.",
    },
    examples=[
        ("Everything and how fresh it is", "SELECT source, first_day, last_day, days, loaded_at FROM analytics.data_coverage ORDER BY source;"),
        ("Days of Meta data not yet covered by Upstack attribution",
         "SELECT m.last_day - u.last_day AS days_behind\nFROM analytics.data_coverage m, analytics.data_coverage u\nWHERE m.source = 'meta_ad_daily' AND u.source = 'upstack_meta_attribution';"),
    ])

rel("analytics.etl_runs", group="analytics", kind="view",
    purpose="Log of every pipeline job run (tracker sync, Meta pull, lifetime pull, status refresh, metrics build, Upstack pull, hook / tag loads). Check it when a number looks stale.",
    grain="one row per job run", source="public.etl_runs (written by etl.db.JobRun on its own autocommit connection)",
    refresh="live, one row per run", coverage="since the first production run (2026-11-04 UTC)",
    caveats=["status is running | ok | failed. A run that crashed leaves 'running'. error is scrubbed of tokens and cut to 500 characters.",
             "stats is job-specific JSON (counts, reconciliation, windows). data_through is set by jobs that load daily data."],
    cols={
        "id": "Run id (monotonic).",
        "job": "Job name: run_daily, tracker_sync, meta_pull, lifetime_pull, status_refresh, build_metrics, upstack_daily, load_tags, load_hooks ...",
        "started_at": "Start time (UTC timestamptz).",
        "finished_at": "Finish time (NULL while running).",
        "status": "running | ok | failed.",
        "data_through": "Last data day the job covered (NULL for jobs without one).",
        "rows_written": "Rows the job wrote, when it counts them.",
        "stats": "Job-specific JSON.",
        "error": "Error text when failed (scrubbed, first 500 characters).",
    },
    examples=[
        ("Last 15 runs", "SELECT id, job, started_at, status, data_through, rows_written FROM analytics.etl_runs ORDER BY id DESC LIMIT 15;"),
        ("Failed runs in the last 7 days", "SELECT id, job, started_at, error FROM analytics.etl_runs WHERE status = 'failed' AND started_at > now() - interval '7 days' ORDER BY id DESC;"),
    ])

# =====================================================================================================================================
# public: Meta
# =====================================================================================================================================

rel("public.ad_daily", group="meta", kind="table",
    purpose="Meta Graph API results per ad per day: the base fact table behind every Meta number. Meta-reported (Meta's own attribution).",
    grain="one row per (ad_id, day)",
    source="Meta Graph API insights level=ad, time_increment=1, both ad accounts (Lonvera Main, Lonvera BackUp); written by etl.meta_pull",
    refresh="nightly (etl service 13:00 UTC, etl.run_daily): the trailing 28 days are re-pulled and replaced in ONE transaction (all or nothing), because Meta restates conversions for weeks",
    coverage="2026-05-06 (app_meta.daily_start, never moves) through yesterday; about 135K rows, 18.7K distinct ads in production",
    caveats=["day is the ad account's OWN calendar day (Main = America/Los_Angeles, BackUp = New York); not re-based.",
             "purchases = Meta action omni_purchase, else purchase, else offsite_conversion.fb_pixel_purchase, under Meta's default attribution (no window is requested); "
             "it counts returning customers. conv_value is the matching action value. Neither is Upstack's new-customer number.",
             "No row filter on spend: purchases booked on days without delivery are kept, so a row can have spend 0 and purchases > 0.",
             "link_clicks is Meta's link_click action (fallback inline_link_clicks). impressions is NULL only on legacy sheet rows (none in production).",
             "landing_page_views = action omni_landing_page_view (else landing_page_view); add_to_carts = omni_add_to_cart (else add_to_cart, else offsite_conversion.fb_pixel_add_to_cart), "
             "same Meta default attribution as purchases. Both are NULL (not 0) on days before they were first pulled; the 2026-11-12 backfill fills them without touching any other column.",
             "initiated_checkouts = omni_initiated_checkout (else initiate_checkout, else offsite_conversion.fb_pixel_initiate_checkout); payment_info_adds = omni_add_payment_info (else add_payment_info, else the pixel event; "
             "Meta returns only add_payment_info today). Both NULL (not 0) before the checkout backfill filled them (fill-only, no other column touched).",
             "meta_family_key / meta_family follow the ad's CURRENT name (build_metrics re-syncs them after a rename).",
             "Rows with source 'sheet' / ad_id 'fam:<family>' were a family-grain backfill; none remain in production.",
             "Reach, frequency, video metrics (thruplays, watch time), demographic / placement breakdowns, ad-level budgets and bids are NOT stored."],
    cols={
        "ad_id": "Meta ad id (text). 'fam:<family key>' on legacy family-grain rows.",
        "day": "Calendar day in the ad account's own timezone.",
        "meta_family_key": "lower(ad family): ad name without ' - Copy N' / merge-tag suffixes. Joins meta_family_map.",
        "meta_family": "The ad family with its original case.",
        "campaign_id": "Meta campaign id ('' = unknown, legacy rows only).",
        "spend": "Meta spend, dollars.",
        "purchases": "Meta-reported purchases (omni_purchase > purchase > pixel purchase), Meta default attribution; includes returning customers.",
        "conv_value": "Meta-reported purchase conversion value, dollars (revenue for Meta ROAS).",
        "link_clicks": "Meta link clicks.",
        "source": "'meta_api' | 'sheet'.",
        "loaded_at": "When the row was written.",
        "impressions": "Meta impressions (NULL = unknown, legacy rows).",
        "landing_page_views": "Meta landing page views (omni_landing_page_view, else landing_page_view). NULL = not pulled yet (days before the 2026-11-12 funnel backfill).",
        "add_to_carts": "Meta adds to cart (omni_add_to_cart, else add_to_cart, else pixel add to cart), the 'Adds to cart' of Ads Manager. NULL = not pulled yet.",
        "initiated_checkouts": "Meta initiated checkouts (omni_initiated_checkout, else initiate_checkout, else pixel initiate checkout). NULL = not pulled yet.",
        "payment_info_adds": "Meta payment info added (omni_add_payment_info, else add_payment_info, else pixel add payment info). NULL = not pulled yet.",
    },
    examples=[
        ("Meta spend and ROAS by day, last 14 days",
         "SELECT day, round(sum(spend), 2) AS spend, sum(purchases) AS purchases, round(sum(conv_value) / NULLIF(sum(spend), 0), 2) AS roas\nFROM ad_daily\n"
         "WHERE day > (SELECT max(day) FROM ad_daily) - 14\nGROUP BY 1 ORDER BY 1 DESC;"),
        ("Top ads of the last 7 days (join meta_ads for names)",
         "SELECT d.ad_id, a.ad_name, round(sum(d.spend), 2) AS spend, round(sum(d.conv_value) / NULLIF(sum(d.spend), 0), 2) AS roas\nFROM ad_daily d JOIN meta_ads a USING (ad_id)\n"
         "WHERE d.day > (SELECT max(day) FROM ad_daily) - 7\nGROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;"),
    ])

rel("public.meta_ads", group="meta", kind="table",
    purpose="One row per Meta ad: name, family, campaign, ad set, status, creation time. The raw dimension behind analytics.meta_ads.",
    grain="one row per ad_id", source="Meta Graph API: names and ids from the daily and all-time insights pulls, status / name / created_time from etl.status_refresh",
    refresh="nightly (etl.run_daily); status is refreshed for every ad that delivered in the last 60 days and every ad not ARCHIVED / DELETED",
    coverage="42K ads: every ad in the daily window plus every ad the all-time pull returned",
    caveats=["first_day / last_day are the first / last ad_daily day of the ad (NULL for the ~23K ads that never delivered since 2026-05-06).",
             "status is Meta's effective_status at status_checked_at.",
             "family_name / meta_family_key are fam(ad_name): ' - Copy N' and ' | x-merge-DATE' suffixes stripped."],
    cols={
        "ad_id": "Meta ad id.",
        "account": "'Lonvera Main' | 'Lonvera BackUp'.",
        "ad_name": "Current ad name.",
        "meta_family_key": "lower(family_name). Joins meta_family_map.meta_family_key and ad_daily.meta_family_key.",
        "campaign_id": "Meta campaign id. Joins meta_campaigns.",
        "adset_id": "Meta ad set id.",
        "adset_name": "Ad set name.",
        "created_date": "Pacific calendar date of created_at.",
        "status": "Meta effective_status.",
        "first_day": "First ad_daily day for the ad.",
        "last_day": "Last ad_daily day for the ad.",
        "created_at": "Exact creation instant (UTC).",
        "family_name": "Ad name with copy / merge suffixes stripped, original case.",
        "status_checked_at": "When status was last read from Graph.",
    })

rel("public.meta_campaigns", group="meta", kind="table",
    purpose="One row per Meta campaign with its stage (testing / scaling). Stage is a naming rule applied on every pull.",
    grain="one row per campaign_id", source="Meta Graph API campaign names seen in the daily and all-time pulls; stage recomputed by etl.build_metrics",
    refresh="nightly (etl.run_daily)", coverage="185 campaigns, 2026-05-06 onward",
    caveats=["stage = 'testing' when lower(name) contains 'test', else 'scaling'. It is a convention of the campaign naming, not a Meta setting.",
             "first_day / last_day = first / last ad_daily day with spend rows for the campaign."],
    cols={
        "campaign_id": "Meta campaign id (text).",
        "account": "Ad account name.",
        "name": "Campaign name.",
        "stage": "'testing' | 'scaling'.",
        "first_day": "First day with ad_daily rows.",
        "last_day": "Last day with ad_daily rows.",
    })

rel("public.meta_ad_lifetime", group="meta", kind="table",
    purpose="All-time Meta totals per ad (Meta-reported), the source of every 'since launch' number.",
    grain="one row per ad_id", source="Meta Graph API insights level=ad over the whole account life (etl.lifetime_pull), both accounts",
    refresh="nightly (etl.run_daily): the table is REPLACED as a whole, only when both accounts reconcile with account-level lifetime spend within 0.5%",
    coverage="41.7K ads, through the last complete day",
    caveats=["Totals cover the ad's whole life, which reaches back before ad_daily's 2026-05-06 start.",
             "tracker_family_key is only filled on legacy sheet-sourced rows; use meta_family_map for the tracker match. source is 'meta_api' for every production row.",
             "Meta-reported (default attribution); purchases include returning customers."],
    cols={
        "ad_id": "Meta ad id.",
        "meta_family_key": "lower(ad family), re-synced to the ad's current name.",
        "tracker_family_key": "Legacy: the sheet tab's Ad Family column (NULL for API rows).",
        "spend": "All-time spend, dollars.",
        "purchases": "All-time Meta-reported purchases.",
        "conv_value": "All-time Meta-reported purchase value, dollars.",
        "impressions": "All-time impressions.",
        "link_clicks": "All-time link clicks.",
        "landing_page_views": "All-time Meta landing page views (omni_landing_page_view, else landing_page_view). NULL until the next nightly lifetime pull.",
        "add_to_carts": "All-time Meta adds to cart (omni_add_to_cart, else add_to_cart, else pixel add to cart). NULL until the next nightly lifetime pull.",
        "initiated_checkouts": "All-time Meta initiated checkouts (omni_initiated_checkout, else initiate_checkout, else pixel initiate checkout). NULL until the next nightly lifetime pull.",
        "payment_info_adds": "All-time Meta payment info added (omni_add_payment_info, else add_payment_info, else pixel add payment info). NULL until the next nightly lifetime pull.",
        "source": "'meta_api' (production) | 'sheet' (legacy).",
        "pulled_at": "When the all-time pull ran.",
        "first_day": "Earliest delivery day if known (legacy).",
    })

rel("public.ad_lifetime", group="meta", kind="table",
    purpose="LEGACY: the sheet-era lifetime snapshot per tracker family (through app_meta.lifetime_cutoff 2026-09-17). Superseded by meta_ad_lifetime; do not use for current numbers.",
    grain="one row per tracker family_key", source="Creative Key sheet tab 'Meta Ads - Lifetime' (frozen snapshot loaded once at backfill)",
    refresh="never (frozen)", coverage="through 2026-09-17",
    caveats=["Kept for history only; since-launch figures now come from meta_ad_lifetime via meta_family_map."],
    cols={
        "family_key": "Tracker family key (lower).",
        "spend": "Lifetime spend through the cutoff, dollars.",
        "purchases": "Lifetime Meta-reported purchases.",
        "conv_value": "Lifetime Meta-reported purchase value, dollars.",
    })

rel("public.meta_family_map", group="meta", kind="table",
    purpose="Maps every Meta ad family to a Creative Tracker family (the resolver's output). The bridge between Meta numbers and tracker tags.",
    grain="one row per meta_family_key", source="etl.resolve_families (ported resolver: exact / relaunch-encoded / ad-name / concept-prefix / product+type+version rules)",
    refresh="nightly (etl.run_daily): new families are mapped and previously unmatched ones retried; existing matches are left alone",
    coverage="16.1K Meta families, 4.1K matched",
    caveats=["tracker_family_key NULL = no tracker match (about 12K families, mostly old ads, ad copies by other names, and non-tracker products).",
             "rule: 'exact' | 'relaunch' | 'adname' | 'prefix' | 'triple' (and 'sheet-windows' for legacy rows); NULL when unmatched."],
    cols={
        "meta_family_key": "lower(Meta ad family). Joins ad_daily.meta_family_key and meta_ads.meta_family_key.",
        "meta_family": "Display name of the Meta family.",
        "tracker_family_key": "Matched tracker family_key (lower), or NULL. Joins tracker_rows.family_key.",
        "rule": "Which resolver rule matched.",
        "resolved_at": "When the row was resolved.",
    })

# =====================================================================================================================================
# public: Upstack
# =====================================================================================================================================

rel("public.upstack_attr_days", group="upstack", kind="table",
    purpose="Registry of the days whose Upstack Meta attribution was pulled completely (a day with zero Meta orders is still a pulled day).",
    grain="one row per day", source="etl.upstack_daily (Upstack /api/query-attribution, any-click, 30-day window, utm_source 'Meta Ads')",
    refresh="nightly (etl service 13:00 UTC): trailing 7 Pacific days re-pulled", coverage="2026-05-06 through yesterday (Pacific), 182 days",
    caveats=["Upstack's own Meta attribution reaches back to 2025-05-13; this app loaded it from 2026-05-06, where its Meta daily data starts."],
    cols={
        "day": "Pacific day.",
        "pulled_at": "When the day was last pulled.",
        "campaigns": "Campaigns with orders or new customers that day.",
        "ad_rows": "Ad-grain attribution rows pulled that day.",
        "api_calls": "Upstack API calls the day took.",
        "nc_campaigns": "New customers Upstack credits to Meta at campaign level (the total).",
        "nc_ads": "The same, summed over the ad rows (the gap vs nc_campaigns = orders credited to a campaign but to no identifiable ad).",
        "note": "Campaigns that came back without an ad split ('unidentified: ...').",
    })

rel("public.upstack_attr_daily", group="upstack", kind="table",
    purpose="Raw Upstack attribution at utm campaign x utm ad NAME grain (as clicked), before mapping to Meta ad ids.",
    grain="one row per (day, campaign, ad) where campaign and ad are utm values", source="Upstack /api/query-attribution (dimension utm_campaign, then utm_ad per campaign), Meta Ads source",
    refresh="nightly: trailing 7 days replaced", coverage="2026-05-06 through yesterday (Pacific), 27.7K rows",
    caveats=["Names, not ids: the same ad name in several ad sets is summed. '(ad not identified)' marks campaigns Upstack could not split by ad.",
             "Any-click, 30-day window: an order is credited to every ad clicked in the 30 days before it.",
             "Revenue is the order total with tax, before refunds."],
    cols={
        "day": "Pacific day of the order.",
        "campaign": "utm_campaign value: usually the campaign NAME, sometimes a raw id or '(not set)'.",
        "ad": "utm_ad value (ad name); '(ad not identified)' when Upstack could not split the campaign by ad.",
        "nc": "orders.new_customers: first-time customers credited.",
        "nc_rev": "orders.new_customer_total_revenue: their first-order revenue, dollars.",
        "orders": "orders.count: all orders credited.",
        "rev": "orders.total_revenue of those orders, dollars.",
    })

rel("public.upstack_campaign_daily", group="upstack", kind="table",
    purpose="Upstack attribution totals per utm campaign and day (used for reconciliation against the ad-level rows).",
    grain="one row per (day, campaign)", source="Upstack /api/query-attribution, dimension utm_campaign, Meta Ads source",
    refresh="nightly: trailing 7 days replaced", coverage="2026-05-06 through yesterday",
    caveats=["Campaign-level totals are higher than the ad-level rows by the orders Upstack credits to no identifiable ad (about 1.3% of orders)."],
    cols={
        "day": "Pacific day.",
        "campaign": "utm_campaign value.",
        "nc": "New customers credited.",
        "nc_rev": "Their first-order revenue, dollars.",
        "orders": "All orders credited.",
        "rev": "Revenue of all credited orders, dollars.",
    })

rel("public.upstack_channel_daily", group="upstack", kind="table",
    purpose="Upstack's own campaign-level ad-platform feed for Meta (spend, platform-reported purchases). Used only as the spend check: it equals Meta's spend to the cent from 2026-08-01.",
    grain="one row per (day, Meta campaign_id)", source="Upstack /api/query-channel-details (campaign dimension, facebookIntegration rows)",
    refresh="nightly: trailing window replaced", coverage="loaded for the backfill window; has no data before 2026-06-05 and a one-day shift between 2026-06-22 and 2026-07-07",
    caveats=["Not a source of truth for Meta spend: use ad_daily. Differences of 1-4% on a few October days come from the BackUp account's New York day."],
    cols={
        "day": "Day as Upstack buckets it (Pacific).",
        "campaign_id": "Meta campaign id.",
        "campaign_name": "Campaign name.",
        "spend": "Spend the feed reports, dollars.",
        "purchases": "Purchases the platform reports (delivered through Upstack).",
        "purchases_value": "Conversion value the platform reports, dollars.",
        "nc_purchases": "Platform-reported new-customer purchases.",
        "nc_purchases_value": "Their value, dollars.",
        "link_clicks": "Link clicks.",
    })

rel("public.upstack_unmapped_daily", group="upstack", kind="table",
    purpose="Upstack attribution rows the build could not tie to a Meta ad. Kept out of every ad, family and tag total; the tie-out reports them.",
    grain="one row per (day, campaign, ad)", source="etl.upstack_build (rebuilt in full on every build)",
    refresh="nightly", coverage="2026-05-06 through yesterday; 521 rows (about 1.3% of orders)",
    caveats=["reason: 'not_identified' (no ad name / '(not set)' / '(ad not identified)'), 'no_campaign' (the utm campaign matches no Meta campaign), 'no_ad' (campaign found, ad name not)."],
    cols={
        "day": "Pacific day.",
        "campaign": "utm_campaign value.",
        "ad": "utm_ad value.",
        "reason": "not_identified | no_campaign | no_ad.",
        "nc": "New customers credited.",
        "nc_rev": "Their first-order revenue, dollars.",
        "orders": "All orders credited.",
        "rev": "Revenue, dollars.",
    })

rel("public.ad_daily_upstack", group="upstack", kind="table",
    purpose="ad_daily-shaped table for the Upstack source: Meta's spend / clicks / impressions per ad and day plus the Upstack orders and new customers credited to each ad.",
    grain="one row per (ad_id, day) for every pulled day, plus rows for ads Upstack credited on days they had no ad_daily row",
    source="etl.upstack_build: Meta spend copied from ad_daily for every pulled day (so a Meta restatement flows through) + Upstack attribution mapped from names to ad ids",
    refresh="nightly (etl.upstack_daily): rebuilt in full from the stored raw pulls and the freshly refreshed ad_daily",
    coverage="2026-05-06 through yesterday (Pacific), 152K rows",
    caveats=["Upstack reports campaign and ad NAMES, not ids. Mapping rules in order: exact ad name inside the utm campaign, same family inside the campaign, exact name in any campaign, "
             "same family anywhere. Where several ads match (one ad copied into several ad sets) the day's credit is split by each ad's Meta spend that day (else lifetime spend, else equally) "
             "and always sums back exactly.",
             "Any-click credits every ad clicked in the 30 days before an order, so ad-level credits overlap and rows exist with spend 0.",
             "purchases / conv_value = ALL attributed orders; nc_purchases / nc_conv_value = first orders only (new customers)."],
    cols={
        "ad_id": "Meta ad id.",
        "day": "Pacific day.",
        "meta_family_key": "lower(ad family) of the ad's current name.",
        "meta_family": "Ad family, original case.",
        "campaign_id": "Meta campaign id ('' when unknown).",
        "spend": "Meta's spend for the ad that day (copy of ad_daily; 0 on credit-only rows), dollars.",
        "purchases": "Upstack orders (new and returning customers) credited to the ad.",
        "conv_value": "Order revenue of those orders, dollars (tax included, before refunds).",
        "nc_purchases": "Upstack new customers (first orders) credited to the ad.",
        "nc_conv_value": "First-order revenue of those new customers, dollars.",
        "link_clicks": "Meta link clicks (copy of ad_daily).",
        "impressions": "Meta impressions (copy of ad_daily).",
        "source": "'upstack'.",
        "loaded_at": "When the table was built.",
    },
    examples=[
        ("Upstack new customers and spend by day",
         "SELECT day, round(sum(spend), 2) AS spend, sum(nc_purchases) AS new_customers, round(sum(nc_conv_value) / NULLIF(sum(spend), 0), 2) AS nc_roas\nFROM ad_daily_upstack\n"
         "WHERE day > (SELECT max(day) FROM ad_daily_upstack) - 14\nGROUP BY 1 ORDER BY 1 DESC;"),
    ])

rel("public.ad_daily_upstack_nc", group="upstack", kind="view", like="public.ad_daily",
    purpose="View over ad_daily_upstack with the exact column set of ad_daily, on the NEW-CUSTOMER basis: purchases = nc_purchases, conv_value = nc_conv_value. "
            "Lets the app run the same SQL on either source.",
    grain="one row per (ad_id, day)", source="public.ad_daily_upstack", refresh="live view", coverage="as ad_daily_upstack",
    caveats=["Spend, clicks, impressions are Meta's; only purchases / conv_value change meaning."],
    cols={"purchases": "Upstack new customers (first orders) credited to the ad.", "conv_value": "Their first-order revenue, dollars.",
          "source": "'upstack'.", "loaded_at": "When ad_daily_upstack was built.", "spend": "Meta's spend (copy), dollars.",
          "meta_family_key": "lower(ad family).", "meta_family": "Ad family, original case.", "campaign_id": "Meta campaign id.",
          "impressions": "Meta impressions (copy).", "link_clicks": "Meta link clicks (copy)."})

rel("public.ad_daily_upstack_all", group="upstack", kind="view", like="public.ad_daily",
    purpose="View over ad_daily_upstack with the exact column set of ad_daily, on the ALL-ORDERS basis: purchases = purchases, conv_value = conv_value (every credited order).",
    grain="one row per (ad_id, day)", source="public.ad_daily_upstack", refresh="live view", coverage="as ad_daily_upstack",
    caveats=["Spend, clicks, impressions are Meta's; only purchases / conv_value change meaning."],
    cols={"purchases": "All Upstack orders credited to the ad.", "conv_value": "Revenue of those orders, dollars.",
          "source": "'upstack'.", "loaded_at": "When ad_daily_upstack was built.", "spend": "Meta's spend (copy), dollars.",
          "meta_family_key": "lower(ad family).", "meta_family": "Ad family, original case.", "campaign_id": "Meta campaign id.",
          "impressions": "Meta impressions (copy).", "link_clicks": "Meta link clicks (copy)."})

rel("public.meta_ad_lifetime_upstack_nc", group="upstack", kind="view", like="public.meta_ad_lifetime",
    purpose="Since-launch per ad on the Upstack new-customer basis = every day held in ad_daily_upstack (Upstack has no all-time endpoint at ad grain).",
    grain="one row per ad_id", source="public.ad_daily_upstack_nc grouped by ad", refresh="live view", coverage="2026-05-06 onward",
    caveats=["'Since launch' here is since 2026-05-06, not the ad's real launch."],
    cols={"purchases": "Upstack new customers over the days held.", "conv_value": "Their first-order revenue, dollars.", "spend": "Meta spend over the days held.",
          "source": "'meta_api'.", "pulled_at": "Latest loaded_at.", "first_day": "First day held for the ad.", "tracker_family_key": "NULL.",
          "impressions": "Impressions over the days held.", "link_clicks": "Link clicks over the days held.", "meta_family_key": "lower(ad family)."})

rel("public.meta_ad_lifetime_upstack_all", group="upstack", kind="view", like="public.meta_ad_lifetime",
    purpose="Since-launch per ad on the Upstack all-orders basis = every day held in ad_daily_upstack.",
    grain="one row per ad_id", source="public.ad_daily_upstack_all grouped by ad", refresh="live view", coverage="2026-05-06 onward",
    caveats=["'Since launch' here is since 2026-05-06, not the ad's real launch."],
    cols={"purchases": "All Upstack orders credited over the days held.", "conv_value": "Their revenue, dollars.", "spend": "Meta spend over the days held.",
          "source": "'meta_api'.", "pulled_at": "Latest loaded_at.", "first_day": "First day held for the ad.", "tracker_family_key": "NULL.",
          "impressions": "Impressions over the days held.", "link_clicks": "Link clicks over the days held.", "meta_family_key": "lower(ad family)."})

# =====================================================================================================================================
# public: Google
# =====================================================================================================================================

rel("public.upstack_google_campaigns", group="google", kind="table",
    purpose="Google Ads campaign dimension from the Upstack channel feed.",
    grain="one row per Google campaign_id", source="Upstack /api/query-channel-details (googleAdsIntegration rows); campaign_type derived from the name by etl.upstack_google.campaign_type",
    refresh="nightly (etl.upstack_daily)", coverage="90 campaigns",
    caveats=["Upstack exposes no campaign type; it is read from the campaign name ('PMax' = Performance Max, 'DG..' / 'IL | Demand Gen' = Demand Gen, 'Standard Shopping' / 'SS |' = Shopping, "
             "'Brand / Prospecting / Competitors Search' = Search, anything else = Other)."],
    cols={
        "campaign_id": "Google campaign id.",
        "name": "Campaign name.",
        "status": "Campaign status as the feed reports it.",
        "bid_strategy": "Bid strategy as the feed reports it.",
        "campaign_type": "Shopping | Search | Performance Max | Demand Gen | Other.",
        "updated_at": "When the row was last refreshed.",
    })

rel("public.upstack_google_channel_daily", group="google", kind="table",
    purpose="Google's own daily numbers per campaign (spend, impressions, clicks, conversions) delivered through Upstack's ad-platform feed.",
    grain="one row per (day, campaign_id); zero-activity days are not stored", source="Upstack /api/query-channel-details, Google rows, campaign grain, Pacific days",
    refresh="nightly: trailing window replaced", coverage="2024-03-25 (first day with Google spend) through yesterday; 8.9K rows",
    caveats=["Google restates conversions after the fact, so purchases / purchases_value of recent days move by up to about 1%.",
             "Google's new-customer measures are empty in this feed: there is no Google-reported new-customer number, only Upstack's attribution."],
    cols={
        "day": "Pacific day.",
        "campaign_id": "Google campaign id.",
        "spend": "Spend, dollars.",
        "impressions": "Impressions.",
        "clicks": "Clicks (all clicks).",
        "purchases": "Purchases Google counts (channel.purchases).",
        "purchases_value": "Conversion value Google reports, dollars.",
    })

rel("public.upstack_google_attr_daily", group="google", kind="table",
    purpose="Raw Upstack attribution for Google Ads at utm campaign NAME grain.",
    grain="one row per (day, campaign)", source="Upstack /api/query-attribution, utm_source 'Google Ads', any-click, 30-day window",
    refresh="nightly: trailing 7 days replaced", coverage="2025-05-13 (first day Upstack has any attribution) through yesterday; 4.8K rows",
    caveats=["Any-click credits Google with orders that also had a Meta click, so Upstack's Google orders exceed Google's own count (about 536 vs 339 over 30 days in Oct 2026)."],
    cols={
        "day": "Pacific day of the order.",
        "campaign": "utm_campaign value as clicked.",
        "nc": "orders.new_customers.",
        "nc_rev": "orders.new_customer_total_revenue, dollars.",
        "orders": "orders.count.",
        "rev": "orders.total_revenue, dollars.",
    })

rel("public.upstack_google_days", group="google", kind="table",
    purpose="Registry of the days whose Google attribution was pulled completely.",
    grain="one row per day", source="etl.upstack_google", refresh="nightly", coverage="2025-05-13 through yesterday, 540 days",
    cols={
        "day": "Pacific day.",
        "pulled_at": "When the day was last pulled.",
        "campaigns": "utm campaigns with attribution that day.",
        "nc": "New customers Upstack credits to Google that day.",
        "orders": "Orders Upstack credits to Google that day.",
    })

rel("public.google_daily", group="google", kind="table",
    purpose="Derived Google campaign x day table: the channel feed rows joined to the attribution rows by campaign name. Behind analytics.google_campaign_daily.",
    grain="one row per (day, campaign_key)", source="etl.upstack_google.build (rebuilt in full from upstack_google_channel_daily + upstack_google_attr_daily)",
    refresh="nightly (etl.upstack_daily)", coverage="2024-03-25 through yesterday (Pacific); 9.4K rows",
    caveats=["Attribution is keyed by campaign NAME; when two campaign ids share a name its numbers go to the one with the most spend.",
             "Before 2025-05-13 the nc / nc_rev / orders / rev columns are 0 because Upstack had no attribution then, NOT because there were no orders; "
             "analytics.google_campaign_daily turns those into NULL.",
             "in_feed = false rows are utm campaigns that match no Google campaign (spend 0)."],
    cols={
        "day": "Pacific day.",
        "campaign_key": "Google campaign id, or 'name:<utm campaign>' for in_feed = false rows.",
        "campaign_name": "Campaign name.",
        "campaign_type": "Shopping | Search | Performance Max | Demand Gen | Other.",
        "in_feed": "true = a campaign of the Google channel feed.",
        "spend": "Google spend, dollars.",
        "impressions": "Impressions.",
        "clicks": "Clicks.",
        "purchases": "Google-reported purchases.",
        "purchases_value": "Google-reported conversion value, dollars.",
        "nc": "Upstack new customers credited (attribution).",
        "nc_rev": "Their first-order revenue, dollars.",
        "orders": "Upstack orders credited.",
        "rev": "Their revenue, dollars.",
    },
    examples=[
        ("Google spend by campaign type, last 30 days",
         "SELECT campaign_type, round(sum(spend)) AS spend, sum(clicks) AS clicks\nFROM google_daily\nWHERE day > (SELECT max(day) FROM google_daily) - 30\nGROUP BY 1 ORDER BY 2 DESC;"),
    ])

# =====================================================================================================================================
# public: orders
# =====================================================================================================================================

rel("public.upstack_orders_daily", group="orders", kind="table",
    purpose="The business's actual revenue and customers per Pacific day, counted once. Numerator of Total NC ROAS. Behind analytics.orders_daily.",
    grain="one row per Pacific day", source="Upstack /api/query, orders.* measures, granularity day, America/Los_Angeles (etl.upstack_orders)",
    refresh="nightly (etl.upstack_daily): trailing 7 days replaced; the job refuses an empty answer", coverage="2025-01-01 through yesterday, 672 days",
    caveats=["Order totals include tax, before refunds. No product / platform dimension. A day with no orders is absent."],
    cols={
        "day": "Pacific day.",
        "nc_rev": "orders.new_customer_total_revenue: first-order revenue, dollars.",
        "rc_rev": "orders.returning_customer_total_revenue: repeat-order revenue, dollars.",
        "nc_orders": "orders.new_customers: number of new customers.",
        "total_rev": "orders.total_revenue, dollars.",
        "orders": "orders.count: all orders.",
        "pulled_at": "When the day was last pulled.",
    },
    examples=[("Last 7 days", "SELECT * FROM upstack_orders_daily ORDER BY day DESC LIMIT 7;")])


# =====================================================================================================================================
# public: creative tracker, tags, hooks, content
# =====================================================================================================================================

rel("public.products", group="creative", kind="table",
    purpose="The product list (Vita Bull, Beef Liver, Boneflex, Vita Belle, Vital Flow, plus 'unassigned') and the rules that route tracker rows and Meta ads to a product.",
    grain="one row per product", source="seeded by migration 001; tag_set is flipped to 'linemap' by etl.load_tags once a product's line-map coverage is high enough",
    refresh="rarely changes", coverage="6 rows",
    caveats=["meta_regex is applied (case-insensitive, lowest meta_priority first) to a Meta family name to guess a product for ads that are on no tracker row.",
             "tag_set: which ad_tags.tag_set drives the hierarchy for the product: 'linemap' (Communication Overview line) or 'tracker' (the tracker's own columns)."],
    cols={
        "id": "Primary key.",
        "slug": "'vita-bull' | 'beef-liver' | 'boneflex' | 'vita-belle' | 'vital-flow' | 'unassigned'.",
        "name": "Display name.",
        "tracker_names": "Values of the Creative Tracker 'Product' column that map to this product (array).",
        "meta_regex": "Regular expression used to guess the product from a Meta family name (NULL for 'unassigned').",
        "meta_priority": "Lower is tested first.",
        "tag_set": "'linemap' | 'tracker'.",
        "sort_order": "Display order.",
        "active": "false hides the product from the Products table.",
    })

rel("public.taxonomy_lines", group="creative", kind="table",
    purpose="The Communication Overview: one line per market cluster x ICP x pain point x emotional trigger for each product (codes L01.. Vita Bull, B01.. Beef Liver, BF01.. Boneflex).",
    grain="one row per line (product_id, line_code)", source="Creative Key sheet tab 'Communication Overview' via etl.load_taxonomy",
    refresh="manual (run etl.load_taxonomy after the sheet changes); not in the nightly cron", coverage="157 lines",
    caveats=["Codes are stable across reloads; a changed trigger keeps its row and code.",
             "added_after_tagging = true marks a line the sheet gained after the ads were tagged: it reads 0 ads by construction, not 'untested'."],
    cols={
        "id": "Primary key.",
        "product_id": "products.id.",
        "line_code": "L01.. / B01.. / BF01.. (unique per product).",
        "cluster": "Market cluster.",
        "icp": "Ideal customer profile / persona.",
        "pain_point": "Pain point.",
        "emotional_trigger": "Emotional trigger (can be NULL).",
        "story": "Short story the ad would tell (15 words or fewer).",
        "cluster_color": "Hex colour of the cluster in the sheet.",
        "sort_order": "Communication Overview order.",
        "added_after_tagging": "true = the line was added after the product's ads were tagged.",
    })

rel("public.line_ingredients", group="creative", kind="table",
    purpose="Ingredient rows that hang off a taxonomy line: what each ingredient attacks and supports.",
    grain="one row per (line, ingredient)", source="Creative Key sheet tab 'Communication Overview' via etl.load_taxonomy",
    refresh="manual with load_taxonomy", coverage="285 rows",
    cols={
        "id": "Primary key.",
        "line_id": "taxonomy_lines.id.",
        "ingredient": "Nutrient / active compound that addresses the pain point (e.g. Zinc, Heme Iron, Glucosamine).",
        "attacks": "'What It Attacks': the mechanism it counters.",
        "supports": "'What It Supports'.",
        "sort_order": "Order within the line.",
    })

rel("public.tracker_rows", group="creative", kind="table",
    purpose="Creative Tracker rows for every product: one row per concept / ad variant with its status, copy, transcript and the tracker's own tags.",
    grain="one row per tracker row (row_key); an ad family (family_key) can span several rows", source="Creative Key Google Sheet tab 'Creative Tracker' via etl.tracker_sync",
    refresh="nightly (etl.run_daily); the sync is refused (no writes) on a renamed required header or a >20% jump in blank families / drop in rows",
    coverage="3,982 rows (3,980 live)",
    caveats=["Identity is row_key = concept_id + '#' + ordinal, never the sheet row. concept_id is text ('0510' is not '510').",
             "family_key = lower(ad_family), or 'id:' + lower(concept_id) when the family is blank; rows with a blank family can never match Meta spend.",
             "Rows the sheet no longer has get deleted_at (never removed); deleted rows are ignored by the metrics.",
             "launch_date is a date typed by the team, not a Meta field."],
    cols={
        "id": "Primary key.",
        "product_id": "products.id.",
        "row_key": "Stable row key ('<concept id>#<n>').",
        "concept_id": "Tracker Concept ID (text).",
        "ad_family": "Tracker 'Ad Family' as typed ('' when blank).",
        "family_key": "The unique-ad key (see caveat).",
        "sheet_row": "Row number on the sheet; informational, used only to order rows (the first row of a family has the lowest).",
        "status": "Tracker status.",
        "ad_type": "VID | IMG.",
        "style": "Creative style.",
        "vendor": "Creative vendor.",
        "fidelity": "Creative fidelity.",
        "proof": "Proof mechanism.",
        "launch_date": "Launch date from the tracker.",
        "concept_name": "Concept name.",
        "description": "Short description.",
        "hypothesis": "Why the concept is tested.",
        "meta_ad_name": "Ad name in Meta (tracker 'Name' column).",
        "primary_text": "Primary text 1-3 (array).",
        "headlines": "Headlines 1-3 (array).",
        "ad_description": "Ad description.",
        "transcript": "Video transcript from the tracker.",
        "asset_url": "Link to the creative asset.",
        "tracker_cluster": "Tracker's Market Cluster.",
        "tracker_icp": "Tracker's ICP (persona).",
        "tracker_icp_legacy": "The older ICP column kept by the team.",
        "tracker_pain": "Tracker's Pain Point.",
        "tracker_emotion": "Tracker's Emotion.",
        "tracker_ingredient": "Tracker's Lead Ingredient.",
        "raw": "Every tracker column by header (jsonb). Free-form; not exposed in analytics.",
        "synced_at": "Last sync that saw the row.",
        "deleted_at": "Set when a sync no longer sees the row; cleared if it reappears.",
        "fingerprint": "md5 of concept name | description | hypothesis | brief date: the stable identity for duplicate (concept, family) groups.",
    })

rel("public.ad_tags", group="creative", kind="table",
    purpose="Tags per tracker row. 'linemap' = the Communication Overview line a tagger chose after looking at the creative; 'tracker' = the tracker's own Market Cluster / ICP / Pain Point.",
    grain="one current row per (tracker_row_id, tag_set); older versions kept with is_current = false",
    source="tag_set 'tracker': etl.tracker_sync on every sync (version 'tracker-live'); 'linemap': etl.load_tags from a tagging cycle file (Sonnet taggers reading the image / video frames + transcript)",
    refresh="'tracker' nightly; 'linemap' by hand per cycle (versions dated 2026-11-03, 2026-11-04, 2026-11-05)", coverage="7.8K rows (3.8K current linemap rows for Vita Bull / Beef Liver / Boneflex)",
    caveats=["Confidence and method matter: 'text only' / 'no picture (copy only)' tags came from copy alone (about 580 rows) and are the weakest; high-confidence image / video tags are the most reliable.",
             "A tracker row with no current tag in the product's tag_set shows as '(untagged)' in the app."],
    cols={
        "id": "Primary key.",
        "tracker_row_id": "tracker_rows.id.",
        "tag_set": "'linemap' | 'tracker'.",
        "version": "Tagging cycle, e.g. 'linemap-v2-2026-11-03', 'tracker-live'.",
        "is_current": "true for the tag in force.",
        "line_id": "taxonomy_lines.id (linemap tags).",
        "cluster": "Market cluster.",
        "icp": "ICP / persona.",
        "pain_point": "Pain point.",
        "emotional_trigger": "Emotional trigger.",
        "confidence": "high | medium | low | NULL.",
        "method": "How it was tagged: 'image', 'video: 4 frames + Whisper transcript', 'text only', 'no picture (copy only)', 'video: thumbnail + no audio' ...",
        "seen": "'What Was Seen': the tagger's description of the creative.",
        "why": "Why the line was chosen.",
        "runner_up": "Second-best line.",
        "copy_only_line": "Line the copy-only first pass picked when different.",
        "tagger": "Who / what tagged it.",
        "tagged_at": "When the tag was written.",
        "extra": "Cycle-specific extras (jsonb): what was heard, gaps, media notes.",
    })

rel("public.ad_hooks", group="creative", kind="table",
    purpose="One hook per tracker row: the ad's first spoken line plus on-image headline boiled down to one short line.",
    grain="one row per tracker_row_id", source="etl.load_hooks from an extraction cycle file (Sonnet reading the opening frame and the first transcript sentences)",
    refresh="manual per cycle; a newer version replaces the older hook of every row it covers", coverage="3.4K rows (about 82% / 87% / 94% of the Vita Bull / Beef Liver / Boneflex tracker rows)",
    caveats=["Rows without a readable source (silent videos, missing thumbnails, no copy) have no hook. About 120 hooks run past 18 words."],
    cols={
        "tracker_row_id": "tracker_rows.id (primary key).",
        "hook": "The one-line hook.",
        "spoken": "First spoken line (video audio / transcript).",
        "on_screen": "On-image headline / first on-screen text.",
        "hook_source": "spoken | on_screen | both | copy | none.",
        "confidence": "high | medium | low.",
        "note": "Extractor remark.",
        "version": "Extraction cycle, e.g. 'hooks-v1-2026-11-05'.",
        "extractor": "Who produced it.",
        "extracted_at": "When the row was written.",
    })

rel("public.family_content", group="creative", kind="table",
    purpose="Per tracker family: the copy Meta runs, transcript, post link. Feeds the ad page of the app.",
    grain="one row per tracker family_key", source="etl.load_assets from a tagging cycle folder (Meta copy, whisper transcripts, tracker transcripts)",
    refresh="manual per cycle", coverage="3.7K families",
    cols={
        "family_key": "Tracker family key (primary key).",
        "meta_body": "Primary text Meta runs.",
        "meta_title": "Headline Meta runs.",
        "meta_desc": "Link description Meta runs.",
        "transcript": "Video transcript used by the taggers.",
        "transcript_seconds": "Length of the transcribed audio.",
        "story_url": "Facebook post link.",
        "is_video": "true for video ads.",
        "ad_id": "A Meta ad id of the family.",
        "video_id": "Meta video id.",
        "source": "'cycle-2026-11-04', 'cycle-2026-11-04:whisper', 'cycle-2026-11-03:tracker' ...",
    })

rel("public.creative_assets", group="creative", kind="table",
    purpose="Creative thumbnails and video frames (image bytes) per tracker family, shown in the app. The only binary data in the database.",
    grain="one row per (family_key, kind, idx)", source="etl.load_assets (tagging cycle media) and etl.assets_pull (Meta Graph thumbnails, best effort)",
    refresh="manual / best effort", coverage="10.6K images",
    caveats=["bytes holds the JPEG itself: never SELECT it casually."],
    cols={
        "id": "Primary key.",
        "family_key": "Tracker family key.",
        "kind": "'thumb' | 'frame'.",
        "idx": "Frame index (0 for a thumb).",
        "mime": "Image mime type.",
        "width": "Pixel width.",
        "height": "Pixel height.",
        "bytes": "The image bytes.",
        "source": "'cycle-2026-11-03' | 'meta-api' ...",
        "created_at": "When loaded.",
    })

# =====================================================================================================================================
# public: derived creative-family roll-ups (rebuilt in full by etl.build_metrics; one set per attribution basis)
# =====================================================================================================================================

rel("public.family_daily", group="rollups", kind="table",
    purpose="ad_daily re-keyed to the TRACKER family (through meta_family_map) and split by campaign stage. Powers any date window in the app without scanning ad_daily.",
    grain="one row per (family_key, day, stage)", source="etl.build_metrics from ad_daily + meta_ads + meta_family_map + meta_campaigns (Meta-reported)",
    refresh="nightly: TRUNCATE and rebuild in the same transaction as the Meta pull", coverage="2026-05-06 through yesterday; only Meta families that map to a tracker family",
    caveats=["Meta families with no tracker match are NOT here (see unmatched_meta), so this table does not sum to the account's spend.",
             "A family mapped by two Meta families sums both. The table is derived: query ad_daily or analytics.meta_ad_daily for account totals.",
             "Traffic columns: clicks_tr and spend_tr are the base for CTR / CPC / CPM: clicks_tr = link clicks on rows that carry impressions, spend_tr = spend on those rows."],
    cols={
        "family_key": "Tracker family key (lower).",
        "day": "Day (Meta ad account day).",
        "stage": "'testing' | 'scaling' | 'unclassified' (days with no campaign).",
        "spend": "Meta spend, dollars.",
        "purchases": "Meta-reported purchases.",
        "revenue": "Meta-reported purchase value, dollars.",
        "link_clicks": "Link clicks.",
        "impressions": "Impressions over rows that carry them.",
        "clicks_tr": "Link clicks on rows with impressions (CTR numerator).",
        "spend_tr": "Spend on rows with impressions (CPC / CPM base).",
    })

rel("public.family_daily_active", group="rollups", kind="table", like="public.family_daily",
    purpose="family_daily restricted to Meta ads whose CURRENT status is ACTIVE ('what the currently active ads did').",
    grain="one row per (family_key, day, stage)", source="etl.build_metrics (ad_daily joined to ACTIVE meta_ads)", refresh="nightly rebuild",
    coverage="2.4K rows: only ads active now (about 441), so recent days mostly", caveats=["Current status is applied to the whole history of the ad."], cols={})

for _b, _lbl in (("nc", "Upstack new customers"), ("all", "Upstack all orders")):
    rel(f"public.family_daily_upstack_{_b}", group="rollups", kind="table", like="public.family_daily",
        purpose=f"family_daily on the {_lbl} basis: spend is Meta's, purchases / revenue are what Upstack credits.",
        grain="one row per (family_key, day, stage)", source=f"etl.build_metrics.rebuild_source('{_b}') from ad_daily_upstack_{_b}", refresh="nightly rebuild",
        coverage="2026-05-06 through yesterday", caveats=["Same family rules as family_daily."],
        cols={"purchases": ("Upstack new customers credited." if _b == "nc" else "Upstack orders credited."),
              "revenue": ("First-order revenue of those customers, dollars." if _b == "nc" else "Revenue of those orders, dollars.")})
    rel(f"public.family_daily_active_upstack_{_b}", group="rollups", kind="table", like="public.family_daily",
        purpose=f"family_daily_active on the {_lbl} basis.",
        grain="one row per (family_key, day, stage)", source=f"etl.build_metrics.rebuild_source('{_b}')", refresh="nightly rebuild",
        coverage="2026-05-06 through yesterday; ACTIVE ads only", caveats=["Same family rules as family_daily_active."],
        cols={"purchases": ("Upstack new customers credited." if _b == "nc" else "Upstack orders credited."),
              "revenue": ("First-order revenue of those customers, dollars." if _b == "nc" else "Revenue of those orders, dollars.")})

rel("public.ad_metrics", group="rollups", kind="table",
    purpose="One row per tracker row with the family's since-launch and trailing-window metrics, carried by the FIRST row of the family (others read 0). Behind the Tags tables of the app.",
    grain="one row per tracker_row_id", source="etl.build_metrics from meta_ad_lifetime + family_daily + meta_family_map + meta_ads (Meta-reported)",
    refresh="nightly: TRUNCATE and rebuild", coverage="3,980 rows, 3,889 first rows",
    caveats=["Metrics sit on the first row of each family_key across ALL products (the tracker row whose product matches the family name, else a real product over 'unassigned', "
             "else the lowest sheet_row); the other rows read 0. Rows with a blank ad family ('id:...') always read 0 because their spend cannot be matched.",
             "Since launch = Meta all-time lifetime per ad summed over the family's Meta ads: no cutoff rule and no launch-date gate. The *_7d / *_30d columns end on the last complete day.",
             "The *_active columns are the same figures over currently ACTIVE ads only."],
    cols={
        "tracker_row_id": "tracker_rows.id (primary key).",
        "product_id": "products.id.",
        "family_key": "Tracker family key.",
        "is_first_row": "true on the row that carries the family's metrics.",
        "launch_date": "Launch date of the row (tracker).",
        "spend_launch": "Since-launch spend, dollars.",
        "purchases_launch": "Since-launch Meta-reported purchases (rounded to whole numbers).",
        "revenue_launch": "Since-launch Meta-reported purchase value, dollars.",
        "spend_7d": "Spend over the 7 days ending the last complete day.",
        "purchases_7d": "Purchases over those 7 days.",
        "revenue_7d": "Purchase value over those 7 days.",
        "spend_30d": "Spend over the 30 days ending the last complete day.",
        "purchases_30d": "Purchases over those 30 days.",
        "revenue_30d": "Purchase value over those 30 days.",
        "live": "true when the family spent on either of the last two days.",
        "built_at": "When the table was built.",
        "impressions_launch": "Since-launch impressions.",
        "clicks_launch": "Since-launch link clicks.",
        "ads_active_count": "ACTIVE Meta ads of the family (same on every row of the family).",
        "spend_launch_active": "Since-launch spend of the ACTIVE ads only.",
        "purchases_launch_active": "Since-launch purchases of the ACTIVE ads only.",
        "revenue_launch_active": "Since-launch purchase value of the ACTIVE ads only.",
        "impressions_launch_active": "Since-launch impressions of the ACTIVE ads only.",
        "clicks_launch_active": "Since-launch link clicks of the ACTIVE ads only.",
        "live_active": "true when the ACTIVE ads of the family spent on either of the last two days.",
    })
for _b, _lbl in (("nc", "Upstack new customers"), ("all", "Upstack all orders")):
    rel(f"public.ad_metrics_upstack_{_b}", group="rollups", kind="table", like="public.ad_metrics",
        purpose=f"ad_metrics on the {_lbl} basis. 'Since launch' = every day held (2026-05-06 on).",
        grain="one row per tracker_row_id", source=f"etl.build_metrics.rebuild_source('{_b}')", refresh="nightly rebuild", coverage="3,980 rows",
        caveats=["Same first-row rule as ad_metrics. purchases_* / revenue_* are Upstack's credit on this basis; spend is Meta's."], cols={})

# Test to scale (migration 015): one view per attribution basis, the data behind the /promotions page (docs/TEST_TO_SCALE.md)
rel("public.creative_stage_moves", group="rollups", kind="table",
    purpose="One row per creative (tracker family) that ever spent: the first day it spent in a Testing campaign and in a Scaling campaign, whether it MOVED from testing to scaling "
            "(testing spend, then a later first scaling day), days in test, its numbers in testing before the move and in scaling since, the last 7 days and whether it is live. "
            "Behind the Test to scale page. Meta's own purchases and revenue; the _upstack_nc / _upstack_all tables read the Upstack attribution. Precomputed (migration 016; it was a view in 015).",
    grain="one row per family_key that has spend (family_daily rows with spend > 0)", source="refresh_creative_stage_moves() from public.family_daily (stage per campaign name: 'test' = testing) + ad_metrics first row + products + ad_tags + taxonomy_lines",
    refresh="nightly (etl.stage_moves, after run_daily and upstack_daily): DELETE + INSERT in one transaction; app_meta stage_moves_built_at / stage_moves_data_through say when", coverage="2026-05-06 through yesterday; about 2.6K creatives, 610 moved on 2026-11-04",
    caveats=["A move is inferred from daily spend, not from an event: Meta cannot move an ad between campaigns, the buyer duplicates the creative into a scaling campaign. "
             "Testing and scaling on the same first day, scaling first and scaling only are NOT moves (see path).",
             "Bulk moves: on 2026-08-22, 2026-10-22 and 2026-10-23 76, 187 and 80 creatives moved in one day (mass relaunches into new scaling campaigns, not test winners). "
             "bulk_move_day marks days with 40 or more moves; the app lets you hide them.",
             "first_day is the first day with spend in any stage: creatives already running on the first day of data (launched_before_data_start, 2026-05-06) have an earlier real launch day, so days_in_test is a lower bound.",
             "test_* counts TESTING-stage rows on days before the first scaling day (all testing days when it never scaled); scale_* counts SCALING-stage rows since the move through the last day. "
             "Testing spend that continues after the move is test_after_spend only.",
             "Spend and link clicks are Meta's in every view; purchases and revenue are Meta-reported here, Upstack's in the _upstack_ views. Not additive across platforms."],
    cols={
        "family_key": "Tracker family key (lower); joins family_daily, ad_metrics, creative_assets.",
        "tracker_row_id": "tracker_rows.id of the family's first row (the one that carries its metrics); the app opens /ad/<id>.",
        "product_id": "products.id of that row (NULL when the family has no first row).",
        "product_slug": "Product slug ('unassigned' for creatives of no product).",
        "product_name": "Product name.",
        "concept_id": "Concept id of the first tracker row.",
        "display": "Display name: the ad family as typed in the tracker, else the concept id.",
        "ad_type": "VID / IMG (tracker).",
        "tracker_status": "Tracker status of the first row, e.g. '8 - Launched'.",
        "tracker_launch_date": "Launch date typed in the tracker (undercounts the older ads; first_day is the one the page uses).",
        "cluster": "Market cluster of the current tag ('(untagged)' when none).",
        "icp": "ICP of the current tag.",
        "pain": "Pain point of the current tag.",
        "data_start": "First day this view's family_daily holds (2026-05-06 today).",
        "data_through": "Last day this view's family_daily holds.",
        "path": "moved | testing_only | scaling_only | same_day (testing and scaling both first spent that day) | scaling_first (scaling before testing) | unclassified_only.",
        "began_in_testing": "true for moved and testing_only: the creatives a test-to-scale rate is about.",
        "moved": "true when path = 'moved': spent in testing and its first scaling day is later.",
        "first_day": "First day with spend in any stage = the launch day as far as the data goes.",
        "testing_first_day": "First day with spend in a Testing campaign (NULL if none).",
        "scaling_first_day": "First day with spend in a Scaling campaign (NULL if none): the day of the move for a moved creative.",
        "last_spend_day": "Last day with spend in any stage.",
        "scaling_last_day": "Last day with spend in a Scaling campaign.",
        "days_in_test": "scaling_first_day - testing_first_day, for moved creatives only.",
        "launched_before_data_start": "true when first_day is the first day of data: the creative was already running and its real launch day is earlier.",
        "test_spend": "Testing-stage spend on days before the first scaling day, dollars.",
        "test_purchases": "Purchases (Meta-reported here, Upstack in the _upstack_ views) on those days.",
        "test_revenue": "Purchase value on those days, dollars.",
        "test_link_clicks": "Link clicks on those days.",
        "test_days": "Days with testing spend before the first scaling day.",
        "test_after_spend": "Testing-stage spend on or after the first scaling day (the original kept running), dollars.",
        "scale_spend": "Scaling-stage spend since the move, through the last day, dollars.",
        "scale_purchases": "Purchases in the Scaling stage.",
        "scale_revenue": "Purchase value in the Scaling stage, dollars.",
        "scale_link_clicks": "Link clicks in the Scaling stage.",
        "scale_days": "Days with scaling spend.",
        "unclassified_spend": "Spend on days with no campaign (stage 'unclassified'), dollars; none today.",
        "last7_spend": "Spend over the last 7 days of data, any stage, dollars.",
        "last7_purchases": "Purchases over the last 7 days, any stage.",
        "last7_revenue": "Purchase value over the last 7 days, any stage, dollars.",
        "last7_scale_spend": "Scaling-stage spend over the last 7 days, dollars.",
        "last7_scale_purchases": "Scaling-stage purchases over the last 7 days.",
        "last7_scale_revenue": "Scaling-stage purchase value over the last 7 days, dollars.",
        "live": "true when the creative spent on the last day of data or the day before (any stage).",
        "live_scaling": "true when it spent in a Scaling campaign on the last day of data or the day before.",
        "moves_that_day": "Creatives that moved on the same scaling_first_day (all creatives, not only this product).",
        "bulk_move_day": "true when moved and 40 or more creatives moved that day: a mass relaunch, not a test winner.",
    },
    examples=[("Creatives that moved to scaling in the last 30 days, with testing and scaling ROAS",
               "SELECT display, product_slug, first_day, scaling_first_day, days_in_test, round(test_spend, 2) AS test_spend,\n"
               "       round(test_revenue / NULLIF(test_spend, 0), 2) AS test_roas, round(scale_spend, 2) AS scale_spend,\n"
               "       round(scale_revenue / NULLIF(scale_spend, 0), 2) AS scale_roas, bulk_move_day\n"
               "FROM creative_stage_moves\nWHERE moved AND scaling_first_day > data_through - 30\nORDER BY scale_spend DESC LIMIT 20;"),
              ("Moves per day and which days were bulk (mass relaunch) days",
               "SELECT scaling_first_day AS day, count(*) AS moves, bool_or(bulk_move_day) AS bulk\nFROM creative_stage_moves\nWHERE moved\n"
               "GROUP BY 1 ORDER BY 2 DESC LIMIT 10;")])
for _b, _lbl in (("nc", "Upstack new customers"), ("all", "Upstack all orders")):
    rel(f"public.creative_stage_moves_upstack_{_b}", group="rollups", kind="table", like="public.creative_stage_moves",
        purpose=f"creative_stage_moves on the {_lbl} basis: the same creatives and the same Meta spend, with purchases / revenue as Upstack credits them.",
        grain="one row per family_key that has spend", source=f"refresh_creative_stage_moves() from public.family_daily_upstack_{_b} (same rules as creative_stage_moves)", refresh="nightly with creative_stage_moves (etl.stage_moves)",
        coverage="2026-05-06 through yesterday", caveats=["Spend, link clicks and the move itself are Meta's; test_purchases / test_revenue / scale_* / last7_* purchases and revenue change meaning.",
                                                         "A creative's first spend day can differ from the Meta view only where Upstack's history starts later than Meta's daily data (both start 2026-05-06 today)."],
        cols={"test_purchases": ("Upstack new customers credited on those days." if _b == "nc" else "Upstack orders credited on those days."),
              "test_revenue": ("First-order revenue of those customers, dollars." if _b == "nc" else "Revenue of those orders, dollars."),
              "scale_purchases": ("Upstack new customers credited in the Scaling stage." if _b == "nc" else "Upstack orders credited in the Scaling stage."),
              "scale_revenue": ("First-order revenue of those customers, dollars." if _b == "nc" else "Revenue of those orders, dollars."),
              "last7_purchases": ("Upstack new customers over the last 7 days." if _b == "nc" else "Upstack orders over the last 7 days."),
              "last7_revenue": ("First-order revenue over the last 7 days, dollars." if _b == "nc" else "Revenue over the last 7 days, dollars."),
              "last7_scale_purchases": ("Upstack new customers in the Scaling stage, last 7 days." if _b == "nc" else "Upstack orders in the Scaling stage, last 7 days."),
              "last7_scale_revenue": ("First-order revenue in the Scaling stage, last 7 days, dollars." if _b == "nc" else "Revenue in the Scaling stage, last 7 days, dollars.")})

rel("public.creative_scaling_placements", group="rollups", kind="table",
    purpose="Where each MOVED creative scales: one row per (creative, scaling campaign, ad set) with the spend, purchases and revenue of its ads there since the move, and how many ads. Behind the ad-set list of the Test to scale page.",
    grain="one row per (family_key, campaign_id, adset_id) for creatives with creative_stage_moves.moved", source="refresh_creative_stage_moves() from ad_daily x meta_ads x meta_campaigns (stage 'scaling') x meta_family_map, days on or after the creative's first scaling day",
    refresh="nightly (etl.stage_moves), with creative_stage_moves", coverage="2026-05-06 through yesterday; about 600 moved creatives",
    caveats=["Spend adds up, per creative, to creative_stage_moves.scale_spend (spend > 0 days only; zero-spend days with purchases are not here).",
             "adset_id / adset are '' for ad_daily rows whose ad has no meta_ads row. Meta-reported purchases / revenue here; the _upstack_ tables hold Upstack's credit."],
    cols={"family_key": "Tracker family key (creative_stage_moves.family_key).", "campaign_id": "Meta scaling campaign id.", "campaign": "Campaign name.",
          "adset_id": "Meta ad set id ('' when unknown).", "adset": "Ad set name.", "ads": "Distinct Meta ads of the creative that spent in this ad set since the move.",
          "spend": "Spend since the move, dollars.", "purchases": "Purchases (Meta-reported here) since the move.", "revenue": "Purchase value since the move, dollars.",
          "first_day": "First day with spend there.", "last_day": "Last day with spend there."})
for _b, _lbl in (("nc", "Upstack new customers"), ("all", "Upstack all orders")):
    rel(f"public.creative_scaling_placements_upstack_{_b}", group="rollups", kind="table", like="public.creative_scaling_placements",
        purpose=f"creative_scaling_placements on the {_lbl} basis: same ad sets and Meta spend, purchases / revenue as Upstack credits them.",
        grain="one row per (family_key, campaign_id, adset_id)", source=f"refresh_creative_stage_moves() from ad_daily_upstack_{_b}", refresh="nightly (etl.stage_moves)",
        coverage="2026-05-06 through yesterday", caveats=["Spend is Meta's; purchases and revenue change meaning."],
        cols={"purchases": ("Upstack new customers credited since the move." if _b == "nc" else "Upstack orders credited since the move."),
              "revenue": ("First-order revenue of those customers, dollars." if _b == "nc" else "Revenue of those orders, dollars.")})

rel("public.scaling_benchmarks", group="rollups", kind="table",
    purpose="What the Scaling campaigns spent and returned over the last 30 days of data, per product and Numbers basis: the benchmark the Test to scale page holds a testing creative against ('What should be scaled': is its testing ROAS or CPA at least as good as its product's scaling average?).",
    grain="one row per (basis, product_slug) with scaling spend in the 30 days", source="refresh_scaling_benchmarks() from public.family_daily (stage 'scaling', and its _upstack_nc / _upstack_all twins) x ad_metrics first row x products",
    refresh="nightly (etl.stage_moves, same transaction as creative_stage_moves): DELETE + INSERT", coverage="3 bases x up to 4 products (incl. 'none'); 30 days ending on the last day of family_daily",
    caveats=["All scaling spend of the product counts, including creatives that moved in a mass relaunch and creatives that never tested.",
             "product_slug 'none' = creatives of no active product (products.slug 'unassigned' or no tracker row). Spend is Meta's in every basis; purchases and revenue are Upstack's in the upstack_* bases (not additive across bases)."],
    cols={"basis": "'meta' (Meta's own reporting), 'upstack_nc' (Upstack new customers) or 'upstack_all' (Upstack all orders).", "product_slug": "products.slug of the creative's product, or 'none'.",
          "day_from": "First day of the 30-day window (day_to - 29).", "day_to": "Last day of family_daily: the window ends there.",
          "creatives": "Creatives (tracker families) with scaling spend in the window.", "spend": "Scaling spend in the window, dollars.",
          "purchases": "Purchases in Scaling campaigns in the window (the basis's meaning: Meta purchases, Upstack new customers, or Upstack orders).",
          "revenue": "Purchase value in the window, dollars (the basis's meaning)."})

rel("public.competitor_brands", group="rollups", kind="table",
    purpose="The competitor brands whose Meta ads are watched weekly (Heart & Soil, Primal Viking, My Marrow ...): exact search phrases for the public Ad Library, the advertiser-name regex that keeps only the brand's own page, and the freshness of the last scrape. Editable: insert / update rows to add or retire a brand (etl.competitor_ads only seeds missing brands).",
    grain="one row per brand", source="seeded by etl.competitor_ads (BRANDS constant), edited by hand; last_* columns written by each run",
    refresh="weekly (Sundays Pacific, etl.competitor_ads)", coverage="18 brands seeded 2026-11-09",
    caveats=["last_error is set when the brand failed in the latest run (blocked, age-gated, layout change); its rows are then carried forward and flagged stale in competitor_concepts.",
             "Brands whose ads are age-gated for logged-out visitors (Hims, Keeps, Men's Edge, Reclaim Co) cannot be read and are not seeded."],
    cols={"brand": "Brand name (primary key), as shown in the views.", "product_type": "Free text: what the brand sells (beef organ capsules, joint supplement ...).",
          "search_phrases": "Exact phrases searched in the Ad Library (brand name, landing domain); results are filtered by name_match.",
          "name_match": "Case-insensitive regex the advertiser (page) name must match; creator ads named 'X with Brand' are dropped.",
          "active": "False retires the brand without deleting its history.", "last_attempt_at": "Last time a run tried this brand.",
          "last_ok_at": "Last time the brand loaded without error.", "last_error": "Reason the last attempt failed; NULL after a good run.",
          "last_ad_count": "Active US ads read in the last good run (a top slice by impressions: capped at about 150 cards per phrase)."})

rel("public.competitor_ads", group="rollups", kind="table",
    purpose="Weekly snapshot of the ACTIVE US Meta ads of each watched competitor, one row per Library ID, read from the public Meta Ad Library with headless Chromium. No spend, impressions or performance exist for these ads: days_running and variant_count are the only strength signals.",
    grain="one row per (week_start, library_id)", source="etl.competitor_ads: public Ad Library cards (exact-phrase search, impressions-sorted, advertiser filtered)",
    refresh="weekly, Sundays Pacific; a failed brand has its previous week copied forward (carried_from set)", coverage="from 2026-11-09; about 100-150 ads per large brand (a top slice, not a census)",
    caveats=["The Library lazy-loads: each phrase returns at most about 150 cards, so large brands (Heart & Soil about 1,000 ads) are a top slice by impressions.",
             "start_date resets when a brand re-uploads an ad, so days_running understates the age of re-uploaded creatives.",
             "format is 'video' when the card has a video, else 'image' (carousels are not distinguishable from images). hook is the first sentence of the copy (video speech and on-image text are not read).",
             "Cards with 'N ads use this creative and text' count N in variant_count."],
    cols={"week_start": "Monday (Pacific) of the week of the run.", "library_id": "Meta Ad Library ID.", "brand": "competitor_brands.brand.", "advertiser": "Page name shown on the card.",
          "start_date": "'Started running on' date (resets on re-upload).", "days_running": "seen_on minus start_date.", "variant_count": "Ads sharing this creative and text (card says 'N ads use this creative and text'), else 1.",
          "format": "'video' or 'image'.", "primary_text": "Ad copy as shown (up to 2,500 characters).", "headline": "Card headline (first line after the landing domain that is not a button label).",
          "hook": "First sentence of the copy, else the headline.", "landing_url": "Landing domain and path (tracking query removed).",
          "concept_key": "Brand + hash of the first 160 characters of normalised copy: groups near-duplicate copy into one concept.",
          "seen_on": "Date the Library was read (Pacific); for carried rows the original date.", "carried_from": "NULL for fresh rows; for carried-forward rows the week_start they were copied from."})

rel("public.competitor_concept_class", group="rollups", kind="table",
    purpose="Lonvera taxonomy classification of each competitor concept (concept_key), made once by headless Claude from the copy text and cached, so only new concepts use plan usage: an existing line (product / cluster / ICP / pain point from the autotag catalogs), a NEW angle with suggested cluster / ICP / pain point, or out of category.",
    grain="one row per concept_key", source="etl.competitor_ads classification step (claude -p, text only, batches of 8 concepts, catalogs etl/autotag/catalogs/*.json)",
    refresh="weekly; concepts already classified are never re-asked", coverage="from 2026-11-09",
    caveats=["Text only: the model never sees the image or video, so low confidence is common for statics whose message is on the image.",
             "line_code is NULL for NEW angles (cluster / icp / pain_point hold the suggestion) and for out-of-category ads (mapped_product 'none')."],
    cols={"concept_key": "competitor_ads.concept_key (primary key).", "brand": "Brand.", "mapped_product": "'vita-bull' | 'beef-liver' | 'boneflex' | 'none': the Lonvera product the angle competes with.",
          "line_code": "Catalog line code (L.. Vital Bull, B.. Beef Liver, BF.. Boneflex) or NULL.", "cluster": "Market cluster (catalog value, or suggested for NEW).",
          "icp": "ICP (catalog value, or suggested for NEW).", "pain_point": "Pain point (catalog value, or suggested for NEW).", "confidence": "high | medium | low.",
          "is_new_angle": "True when no catalog line covers the angle.", "new_angle_label": "4-8 word label of a NEW angle.", "why": "The model's reason, 15 words or fewer.",
          "model": "Model id used.", "classified_at": "When it was classified."})

rel("public.competitor_concepts", group="rollups", kind="view",
    purpose="What competitors run on Meta right now, grouped by concept (near-duplicate copy), with the Lonvera line each maps to. The contract read by the weekly Creative priorities one-sheeter. Latest week: WHERE week_start = (SELECT max(week_start) FROM competitor_concepts).",
    grain="one row per (week_start, brand, concept_key)", source="public.competitor_ads x competitor_brands x competitor_concept_class",
    refresh="weekly (Sundays Pacific)", coverage="from 2026-11-09",
    caveats=["No spend, impressions or ROAS: days_running (max across the concept's ads) and variant_count (ads sharing the creative) are proxies, and start dates reset on re-upload.",
             "is_stale = the brand failed to load this week and the rows are the last good snapshot carried forward, or the data is older than 8 days.",
             "Unclassified concepts show confidence 'low' and NULL mapped_product / line_code. For NEW angles cluster / icp / pain_point are the model's suggestion."],
    cols={"week_start": "Monday (Pacific) of the run week.", "brand": "Brand.", "concept_key": "Concept (near-duplicate copy of one brand).",
          "first_ad_start_date": "Earliest start date among the concept's ads.", "days_running": "Max days running among the concept's ads (as of as_of).",
          "variant_count": "Ads sharing the creative / copy, summed over the concept's cards.", "format": "'video' or 'image' of the longest-running ad.",
          "hook": "First sentence of the longest-running ad's copy.", "primary_text_excerpt": "First 280 characters of that ad's copy.",
          "mapped_product": "'vita-bull' | 'beef-liver' | 'boneflex' | 'none' | NULL.", "line_code": "Lonvera catalog line, NULL for NEW / out-of-category / unclassified.",
          "cluster": "Market cluster.", "icp": "ICP.", "pain_point": "Pain point.", "confidence": "high | medium | low (unclassified = low).",
          "is_new_angle": "No catalog line covers it.", "new_angle_label": "Label of the NEW angle.", "as_of": "Date the Library was read.", "is_stale": "Carried forward or older than 8 days."})
rel("public.competitor_posts", group="rollups", kind="table",
    purpose="Non-Meta chatter about watched competitors and the category: Reddit posts (public subreddit RSS) and news headlines (Google News RSS) that mention a competitor_brands brand or a category term (beef organ supplement, bone marrow supplement ...). Read by the /competitors page next to the Meta ad snapshots.",
    grain="one row per (source, url)", source="etl.competitor_chatter: Google News RSS (one query per active brand plus category terms) and public subreddit RSS (/r/<sub>/new/.rss), filtered to brand / category mentions, spam dropped, last 14 days per run",
    refresh="by hand / when scheduled: python -m etl.competitor_chatter [--days 14]; idempotent upsert, older rows are kept as history", coverage="from 2026-11-12; only what the public feeds still show (Reddit RSS lists the newest 100 posts per subreddit)",
    caveats=["Reddit search is blocked for logged-out clients, so Reddit rows come only from the newest posts of a short subreddit list that mention a brand or category term; a quiet week or a Reddit block (it blocks datacenter IPs and allows about 1 request a minute) shows few or no rows.",
             "score and num_comments are 0 for every row: the RSS feeds carry neither. Do not read 0 as no engagement.",
             "google_news summary holds the publisher name; the headline link is a Google News redirect. Brand is the brand searched or mentioned in the headline; NULL = category-level hit."],
    cols={"week_start": "Monday (Pacific) of the week the item was posted.", "source": "'google_news' | 'reddit'.", "brand": "competitor_brands.brand the item mentions, NULL for a category-term hit.",
          "title": "Post title / headline (publisher suffix removed).", "url": "Link (primary key with source); Google News links are redirects.", "subreddit": "Subreddit name for reddit rows, else NULL.",
          "score": "Upvotes: always 0 (not in the RSS feeds).", "num_comments": "Comment count: always 0 (not in the RSS feeds).", "posted_at": "When the item was posted / published.",
          "summary": "Reddit: first 400 characters of the post text; Google News: the publisher name.", "seen_on": "Date (Pacific) of the run that last saw the item."})
rel("public.competitor_ad_media", group="rollups", kind="table",
    purpose="Creative thumbnails of the watched competitors' Meta ads (the image, or the poster frame of a video) so the /competitors page can show what the ads look like. Bytes are stored because the Ad Library image links are signed and expire within days.",
    grain="one row per Meta Ad Library ID (joins to competitor_ads.library_id)", source="etl.competitor_ads: downloaded from the public Ad Library at scrape time for a curated slice per brand (top 12 longest-running distinct concepts + up to 8 new this week, at most 400 per run), or by --media-backfill for ads already stored; re-encoded to JPEG, at most 480 px wide and 150 KB",
    refresh="weekly with etl.competitor_ads (Sundays Pacific); python -m etl.competitor_ads --media-backfill fills ads already stored; rows older than 60 days that are not in the latest week are deleted", coverage="from 2026-11-12; only the curated slice per brand, not every ad in competitor_ads",
    caveats=["Not every competitor_ads row has media: only the curated slice, and only when the card showed an image and the download worked. No row = no thumbnail, not a missing ad.",
             "Served by GET /api/competitors/media/<library_id> (login required); do not read bytes through SQL for display.",
             "Carousels store the first image only; videos store the poster frame, never the video."],
    cols={"library_id": "Meta Ad Library ID (primary key).", "kind": "'image' or 'video_poster'.", "content_type": "MIME type of bytes (image/jpeg).", "width": "Pixel width after re-encoding.",
          "height": "Pixel height after re-encoding.", "bytes": "The JPEG bytes.", "fetched_at": "When the image was downloaded."})
rel("public.competitor_landing_pages", group="rollups", kind="table",
    purpose="The landing pages the watched competitors' Meta ads point to, opened once a week and tagged: page type, the page's key theme, hero headline, selling points, offer, price, guarantee, proof, urgency devices, objections handled, CTA, and Lonvera's cluster / ICP / pain point, so we can learn from the pages and not only the ads.",
    grain="one row per (week_start, lp_id): one distinct landing page per week, however many ads point to it (week_start = Monday, Pacific, the competitor_ads week)",
    source="etl.competitor_landing_pages: distinct competitor_ads.landing_url of the week, normalised to url_key (host without www + path, tracking parameters dropped), opened with headless Chromium (Playwright; 1280x900 viewport, 3 s between pages, 20 s timeout, no login, no captcha or bot-wall bypass), visible text extracted, tagged by headless Claude (Sonnet 5.5) from the text and the screenshot in batches of 5",
    refresh="by hand for now (not scheduled in the nightly etl yet); python -m etl.competitor_landing_pages [--week --brand --limit --retag --no-tag --dry-run]; a page already tagged this week is skipped",
    coverage="from the first run after migration 261 (2026-11-13 build); at most 200 distinct pages per run, the most-used ones first; one row per week kept as history",
    caveats=["Not every ad has a page row: ads without a landing_url, on social hosts (facebook, instagram, youtube), or past the 200-page cap are unlinked in competitor_ad_lp.",
             "http_status / fetch_error show a blocked page (403, challenge, blank): its tag columns stay NULL. Blocked is not a missing page; the Ad Library may still show the ad.",
             "landing_url in competitor_ads is often only the display domain and path (the Ad Library card shows no query string), so the page opened is the display URL, which can differ from the exact page a click lands on (tracking redirects are followed; final_url is where it ended).",
             "content_hash covers the extracted key text (title, headings, bullets, prices, guarantee, reviews, FAQ, CTA) with clocks and counters removed; changed_from_prior compares it with the latest earlier week of the same lp_id (false when there is none) and a changed hash can still be an A/B variant served on the visit. change_note is a model summary written only for changed pages.",
             "Tags are the model's reading of one visit, quoted not invented; unknown fields are NULL. method 'vision_text' = the screenshot and the text, 'text' = text only (no screenshot)."],
    cols={"week_start": "Monday (Pacific) of the competitor_ads week (primary key with lp_id).", "lp_id": "First 16 hex characters of sha1(url_key) (primary key with week_start); the same page across ads and weeks keeps one lp_id.",
          "url_key": "Normalised page key: lowercase host without www + path without trailing slash; tracking parameters dropped, only page-selecting parameters (variant, v, offer ...) kept.",
          "final_url": "Where the page ended after redirects, tracking parameters removed; NULL when it never loaded.", "brand": "competitor_brands.brand with the most ads pointing to this page.",
          "http_status": "HTTP status of the main document; NULL when it never answered (timeout, DNS).", "fetched_at": "When the page was opened.",
          "ad_count": "How many of the week's competitor_ads rows point to this page.", "page_type": "product page | advertorial or article | listicle or ranked list | quiz or funnel | video sales page | homepage or collection | other.",
          "key_theme": "One plain sentence (<= 20 words): the page's central promise or story.", "hero_headline": "The page's main headline, quoted exactly.",
          "selling_points": "3 to 6 short phrases in the page's own terms (ingredients, doses, sourcing, benefits, comparisons).", "offer_summary": "Price, bundle, discount, free shipping, subscription as offered.",
          "price_text": "Price as shown on the page.", "guarantee": "Guarantee as shown (e.g. '60 day money back').",
          "proof_elements": "From: reviews count or stars, doctor or expert, clinical study, press logos, before and after, UGC or testimonials, certifications, founder story, other.",
          "urgency_devices": "countdown, low stock, limited offer, free gift deadline, or none.", "objections": "FAQ topics or objection handling the page addresses.", "cta": "Main call-to-action button text.",
          "cluster": "Lonvera market cluster of the page's angle (from the taxonomy; a model suggestion for a new angle).", "icp": "Lonvera ICP the page speaks to.", "pain_point": "Lonvera pain point the page speaks to.",
          "why_it_works": "One short sentence on what the page does well for its buyer.", "method": "'vision_text' (screenshot + text) or 'text'.", "confidence": "high | medium | low: how clearly the page states its buyer and promise.",
          "content_hash": "sha1 of the normalised key text of the page (clocks and counters removed).", "changed_from_prior": "True when content_hash differs from the latest earlier week of the same lp_id.",
          "change_note": "One plain line on what changed, only for changed pages with a tagged earlier week.", "fetch_error": "Why the page could not be read (blocked, challenge, blank, timeout, DNS); NULL when read.",
          "model": "Model that tagged the page.", "tagged_at": "When the tags were written; NULL = captured but not tagged."})
rel("public.competitor_lp_media", group="rollups", kind="table",
    purpose="One above-the-fold screenshot of each competitor landing page per week, so the /competitors page can show what the page looks like.",
    grain="one row per (week_start, lp_id) (joins to competitor_landing_pages)", source="etl.competitor_landing_pages: viewport screenshot (1280x900) taken after the page loaded, re-encoded to JPEG, at most 640 px wide and 90 KB",
    refresh="weekly with the landing page run; rows older than 60 days that are not in the latest week are deleted", coverage="from the first run after migration 261; only pages that loaded",
    caveats=["No row = no screenshot (blocked, blank or failed page), not a missing page.", "Served by GET /api/competitors/lp-media/<lp_id>?week=YYYY-MM-DD (login required); do not read bytes through SQL for display.",
             "A cookie banner or popup can cover part of the shot; only an obvious Accept / Close button is clicked."],
    cols={"week_start": "Monday (Pacific) of the week (primary key with lp_id).", "lp_id": "Landing page id (primary key with week_start).", "content_type": "MIME type of bytes (image/jpeg).",
          "width": "Pixel width after re-encoding.", "height": "Pixel height after re-encoding.", "bytes": "The JPEG bytes.", "fetched_at": "When the screenshot was stored."})
rel("public.competitor_ad_lp", group="rollups", kind="table",
    purpose="Links each competitor ad of a week to the landing page it points to, so an ad can show its page and a page can list its ads.",
    grain="one row per (week_start, library_id) (joins to competitor_ads and to competitor_landing_pages on lp_id)", source="etl.competitor_landing_pages: competitor_ads.landing_url of the week normalised to lp_id",
    refresh="weekly with the landing page run (rewritten for the week on every run)", coverage="from the first run after migration 261",
    caveats=["Only ads with a usable landing_url on a non-social host and within the 200-page cap are linked; a missing row means unlinked, not 'no landing page'.",
             "Several ads usually share one page, so count ads with competitor_landing_pages.ad_count, not rows here joined to the pages table."],
    cols={"week_start": "Monday (Pacific) of the competitor_ads week (primary key with library_id).", "library_id": "Meta Ad Library ID (primary key with week_start).", "lp_id": "competitor_landing_pages.lp_id of the ad's page."})
rel("public.competitor_ad_video", group="rollups", kind="table",
    purpose="A small playable copy of the watched competitors' video ads (first 30 seconds, H.264 / AAC mp4) so the /competitors lightbox can play them in the app. Bytes are stored because the Ad Library video links are signed and expire in about 4 days.",
    grain="one row per Meta Ad Library ID (joins to competitor_ads.library_id and competitor_ad_media.library_id)", source="etl.competitor_ads: the SD mp4 link on the public Ad Library card (scrape time or --video-backfill), downloaded with a plain GET, trimmed to 30 seconds with ffmpeg stream copy (re-encoded only when the source is not H.264 / AAC or is over the cap), at most 6 MB each; selection = the same curated slice as the thumbnails, videos only",
    refresh="weekly with etl.competitor_ads (Sundays Pacific); python -m etl.competitor_ads --video-backfill fills ads already stored; rows older than 21 days that are not in the latest week are deleted, and the table is held under --max-video-mb (default 300)", coverage="from 2026-11-13; only the curated video slice, not every video in competitor_ads",
    caveats=["No row = no saved video (never captured, over the size cap, or the link was gone), not a missing ad. The lightbox then shows the poster and a link to the Ad Library.",
             "Served by GET /api/competitors/video/<library_id> (login required, HTTP Range supported); do not read bytes through SQL for display.",
             "Only the first 30 seconds are kept (duration_s is the stored length, not the ad's length). Quality is Meta's SD rendition (360 px wide)."],
    cols={"library_id": "Meta Ad Library ID (primary key).", "content_type": "MIME type of bytes (video/mp4).", "duration_s": "Stored length in seconds (at most 30); NULL when ffmpeg was not available to read it.",
          "width": "Pixel width of the stored video, NULL when unknown.", "height": "Pixel height of the stored video, NULL when unknown.", "bytes": "The mp4 bytes (moov atom first, so it streams and seeks).",
          "size_bytes": "Length of bytes (kept as a column so the size cap never reads the bytes).", "fetched_at": "When the video was downloaded."})
rel("public.competitor_ad_transcript", group="rollups", kind="table",
    purpose="What is said in the saved competitor video ads: a speech-to-text transcript of the first 30 seconds of each video in competitor_ad_video, so the ads can be read and tagged, not only watched.",
    grain="one row per Meta Ad Library ID (joins to competitor_ads.library_id and competitor_ad_video.library_id)", source="etl.competitor_tags: faster-whisper (model base, int8, CPU, voice-activity filter) on the stored mp4 audio",
    refresh="with etl.competitor_tags (by hand: python -m etl.competitor_tags; ads already transcribed are skipped, --retag redoes them)", coverage="from 2026-11-13; only videos that have a row in competitor_ad_video",
    caveats=["transcript is an empty string when the video has no speech (music only or silent); no row = not transcribed (faster-whisper not installed on that machine, or the transcription failed), not a silent ad.",
             "Only the first 30 seconds (the stored copy), in Meta's SD audio; names and product terms can be misheard."],
    cols={"library_id": "Meta Ad Library ID (primary key).", "transcript": "Speech-to-text of the saved video; '' = no speech.", "language": "Detected language code (en ...).", "duration_s": "Audio length in seconds that was read.",
          "model": "Transcription model, for example faster-whisper-base-int8.", "created_at": "When it was transcribed."})
rel("public.competitor_ad_tags", group="rollups", kind="table",
    purpose="Tags of each curated competitor ad (one with a saved picture or video), made by headless Claude LOOKING at the picture (video: cover plus 4 frames), the transcript and the copy: Lonvera's own taxonomy (line, cluster, ICP, pain point, emotional trigger), the Creative Tracker's style / fidelity / proof mechanism / emotion values (so competitor and Lonvera ads compare 1:1) and learning tags (hook type, offer type, device, call to action, hook text, why it works, what was seen and said).",
    grain="one row per Meta Ad Library ID (joins to competitor_ads.library_id; concept_key joins to competitor_concept_class)", source="etl.competitor_tags: claude -p (Sonnet, Read tool only) on batches of 6 ads, validated against taxonomy_lines and the distinct Creative Tracker values",
    refresh="with etl.competitor_tags (python -m etl.competitor_tags; ads already tagged at the current tag_version are skipped, --retag replaces the row)", coverage="from 2026-11-13; only the curated slice that has a saved picture or video, not every ad in competitor_ads",
    caveats=["method says what the tagger had: vision_transcript (picture and speech), vision (picture, no speech or not transcribed) or text (no picture could be read, confidence capped at medium). The tags are the model's reading, not a measured result: no spend or performance exists for competitor ads.",
             "line_code is NULL for a new angle (is_new_angle, new_angle_label; cluster / icp / pain_point hold the suggestion) and for out-of-category ads (cluster NULL too). style / fidelity / proof_mechanism / emotion hold a Creative Tracker value, or 'PROPOSE: <value>' when none fitted.",
             "competitor_concept_class stays the text-only, per-concept classification; the two can disagree, and this table (which saw the picture) is the better read for a single ad."],
    cols={"library_id": "Meta Ad Library ID (primary key).", "concept_key": "competitor_ads.concept_key of the ad.", "tag_version": "Prompt / list version that made the row (ctags-v1-2026-11-13).",
          "method": "vision_transcript | vision | text: what the tagger saw.", "confidence": "high | medium | low (the tagger's own).", "line_code": "Lonvera line (L.. Vital Bull, B.. Beef Liver, BF.. Boneflex) or NULL.",
          "cluster": "Market cluster (taxonomy value, or suggested for a new angle).", "icp": "ICP (taxonomy value, or suggested).", "pain_point": "Pain point (taxonomy value, or suggested).",
          "emotional_trigger": "The line's emotional trigger (or suggested for a new angle).", "is_new_angle": "True when no Lonvera line covers the angle.", "new_angle_label": "4-10 word label of the new angle.",
          "style": "Creative Tracker Style value, or 'PROPOSE: x'.", "fidelity": "Creative Tracker Creative Fidelity value, or 'PROPOSE: x'.", "proof_mechanism": "Creative Tracker Proof Mechanism value, or 'PROPOSE: x'.",
          "emotion": "Creative Tracker Emotion value, or 'PROPOSE: x'.", "hook_type": "question | stat or number | story | call-out | comparison | authority or doctor | myth-bust | curiosity | social proof | offer-led | other.",
          "offer_type": "discount | bundle or multi-pack | free shipping | guarantee | free gift | subscription | none shown | other.",
          "device": "testimonial | listicle or ranked list | before and after | founder or brand story | doctor or expert | podcast clip | demo or unboxing | news-style | comparison chart | text-on-image | other.",
          "cta": "Call to action, 8 words or fewer.", "hook_text": "First line seen or said, quoted, 20 words or fewer.", "why_it_works": "One plain sentence, 25 words or fewer: what Lonvera could learn from it.",
          "seen_on": "What the tagger saw on screen (the evidence).", "said_on": "What was said (the evidence), or 'no speech'.", "model": "Model id used.", "tagged_at": "When the row was written."})
rel("public.creative_priorities_snapshots", group="creative", kind="table",
    purpose="The weekly creative priorities one-sheeter: per product, the market cluster / ICP / pain point / story lines to iterate on (ads that work) and to produce for the first time, with the number of new concepts to brief and the numbers behind each row. One snapshot per week, kept as produced.",
    grain="one row per week (week_start = the Sunday, Pacific)", source="etl.creative_priorities from ad_metrics, family_daily (and the _upstack_nc twin), creative_stage_moves, ad_tags (linemap), taxonomy_lines, line_ingredients, ad_hooks",
    refresh="Sundays, in the nightly etl (gated on Pacific time, non-fatal); by hand with python -m etl.creative_priorities [--week YYYY-MM-DD]; re-running a week replaces its snapshot",
    coverage="from the week the migration was deployed; one row per Sunday",
    caveats=["Scored on Meta-reported numbers; the Upstack new-customer ROAS only raises a flag. Prioritisation, not a causal claim.",
             "rows is a JSON array; each element carries section ('iterate' | 'new'), product, rank, the line (cluster, icp, pain_point, story), concepts, score, confidence, flags, why, metrics and hooks. Scoring and thresholds: docs/CREATIVE_PRIORITIES.md."],
    cols={"week_start": "The Sunday (Pacific) the snapshot belongs to (primary key).", "built_at": "When the snapshot was last written.",
          "data_through": "Last day of Meta data the snapshot used.", "basis": "Numbers basis of the scoring: 'meta' (Meta-reported).",
          "thresholds": "JSON object: every threshold and weight constant of etl/creative_priorities.py as used for this snapshot.",
          "baselines": "JSON object by product slug: the product's own averages (ROAS since launch / last 30 days, CPA, hit rate, new ads in 30 days) the lines are compared with.",
          "rows": "JSON array of the ranked rows (iterate and new sections, all products).", "stats": "JSON object: candidate counts, skipped counts, tagged and untagged spend, data_through."})

rel("public.creative_priorities_views", group="creative", kind="table",
    purpose="Saved views of the creative priorities sheet: the rows someone selected on /priorities, frozen with the snapshot's baselines and legend, opened by an unguessable link /priorities/v/<token>.",
    grain="one row per saved view (token is unique)", source="app/creative_priorities.py (Save view on /priorities), copied from creative_priorities_snapshots",
    refresh="written on demand by the app; never rebuilt", coverage="from the day migration 198 was applied",
    caveats=["payload is a frozen copy: a view does not change when a later snapshot replaces its week. The link is the access (the app has no password)."],
    cols={"id": "Surrogate key (primary key).", "token": "Unguessable link token (unique).", "name": "View name, '' when unnamed; shown as the page title and used in the CSV file name.",
          "week_start": "The snapshot week (Sunday, Pacific) the rows came from.", "keys": "Stable row keys of the selected rows.",
          "payload": "JSON object: the selected rows plus the snapshot's baselines, legend and competitor as-of date.", "created_at": "When the view was saved.",
          "kind": "'rows' (a fixed set of selected rows, shown with the latest numbers where the row still exists) or 'filters' (a saved filter state applied live to the current snapshot).",
          "filters": "JSON object for kind 'filters': products (slugs), sections (keys), q (search text), key and dir (sort). NULL for kind 'rows'.",
          "updated_at": "When the view was last replaced by Update this view; NULL if never.",
          "briefed": "JSON object: stable row key (product|section|cluster|icp|pain_point, or manual:<id>) -> {at, note} for the concepts marked briefed in this view; absent = not briefed."})

rel("public.creative_priorities_manual", group="creative", kind="table",
    purpose="Concepts added by hand on /priorities (Add concept): product, market cluster, ICP, pain point, story, notes and a why-now line. Not tied to a weekly snapshot: a row stays on the sheet every week until it is marked done or deleted.",
    grain="one row per manual concept", source="the /priorities page via POST /api/priorities/manual (app/priorities_manual.py)", refresh="on demand (a person adds, edits, marks done or deletes)", coverage="from the day the migration was deployed",
    caveats=["No author column: the app has no login of its own, only timestamps.",
             "A cluster / ICP / pain point / story that is not in the Communication Overview catalog is stored as typed and flagged with the new_* columns (a proposal, not a line of ours)."],
    cols={"id": "Primary key (bigserial).", "product": "'vita-bull' | 'beef-liver' | 'boneflex'.", "section": "Page section the concept sits in: 'iterate' | 'faded' | 'new' | 'other'.",
          "cluster": "Market cluster (catalog value or typed).", "icp": "ICP (catalog value or typed).", "pain_point": "Pain point (catalog value or typed).", "story": "Story; empty when none.",
          "notes": "Free-text notes, up to 2,000 characters, line breaks kept.", "why_now": "Optional short why-now / what-to-test line, up to 300 characters.", "status": "'open' (shown) or 'done' (hidden until Show done is ticked).",
          "new_cluster": "True when the cluster is not in the catalog for that product.", "new_icp": "True when the ICP is not in the catalog under that cluster.",
          "new_pain_point": "True when the pain point is not in the catalog under that ICP.", "new_story": "True when the story is typed text not in the catalog for that pain point.",
          "created_at": "When the concept was added.", "updated_at": "When it was last edited or its status changed.", "done_at": "When it was marked done; NULL while open."})

rel("public.creative_priorities_assignments", group="creative", kind="table",
    purpose="Production assignments from /priorities: a concept given to a vendor to make for a week (Assign to vendor), with the brief link, the creative link, the stage timestamps and the Creative Tracker Concept IDs of the ads that came out of it. The concept itself stays in the ideation list unchanged.",
    grain="one row per concept x vendor x week (open rows are unique; a dropped row can be assigned again)", source="the /priorities and /priorities/production pages and the vendor link page via /api/priorities/assignments (app/priorities_assign.py)",
    refresh="on demand (a person assigns, adds links, marks ready or launched, drops; a vendor adds a creative link and a note)", coverage="from the day the migration was deployed",
    caveats=["The board column is the stored status (migration 245); the older stage field is derived from it for compatibility. A linked Creative Tracker row can pull the status forward, never back.",
             "Nothing here is written to the Google Sheet or Meta. The tag text is copied from the row at assign time, so history survives a taxonomy reload; line_id has no foreign key on purpose.",
             "created_by and updated_by hold the signed-in user, or vendor:<name> for a write made on a vendor link page."],
    cols={"id": "Primary key (bigserial).",
          "concept_key": "Stable concept handle: 'line:<taxonomy_lines.id>' when the row has a line, 'manual:<id>' for a manual concept, else 'text:<product>|<cluster>|<icp>|<pain point>' (lowercased, spaces collapsed).",
          "text_key": "The text form of the key ('text:<product>|<cluster>|<icp>|<pain point>'), always stored, so a proposal that later becomes a real line still matches.",
          "product": "'vita-bull' | 'beef-liver' | 'boneflex'.", "vendor": "Vendor, spelled as in the Creative Tracker Vendor column (Madre, Victoria, TrybeDCShop, TrybeOrion, 10xROAS, Internal, PuredMedia, CreateMastery, OpenProgram).",
          "line_id": "taxonomy_lines.id at assign time; NULL for manual or proposed concepts. No foreign key.", "manual_id": "creative_priorities_manual.id for a manual concept; NULL otherwise.",
          "line_code": "Line code (for example BL12) at assign time; empty when none.", "cluster": "Market cluster at assign time.", "icp": "ICP at assign time.", "pain_point": "Pain point at assign time.",
          "story": "Story at assign time; empty when none.", "section_at_assign": "Action the concept had when assigned: 'revive' | 'make_more' | 'test' | 'add' | 'stop' | 'hold' (rows written before 2026-11-10 hold the old section names 'faded' | 'iterate' | 'new' | 'proposal' | 'competitor' | 'rest' | 'other', shown as the new Action).",
          "note": "Note typed when assigning or changing the assignment.", "vendor_note": "Note typed by the vendor on their own link page.",
          "for_week": "The Monday of the week the vendor works on it (a CHECK enforces a Monday).", "brief_url": "Link to the brief; NULL until added.", "creative_url": "Link to the finished creative; NULL until added.",
          "brief_at": "When the brief link was first added; NULL until then.", "creative_at": "When the creative link was first added; NULL until then.",
          "ready_at": "When someone clicked Ready to launch; NULL if not (or cleared).", "launched_at": "When it was marked launched by hand; NULL if not. A linked tracker row with status 8 or 9 also counts as launched.",
          "dropped_at": "When the assignment was dropped (soft cancel); NULL while active. Rows are never deleted.",
          "tracker_concept_ids": "Creative Tracker Concept IDs of the ads made from this assignment, entered by hand.", "created_at": "When the assignment was created.", "updated_at": "When it was last changed.",
          "created_by": "Who created the assignment: the signed-in user name; NULL when none.",
          "updated_by": "Who last changed it: the signed-in user name, or vendor:<name> for a write made on a vendor link page; NULL when none.",
          "format_code": "Creative format chosen when assigning or later (creative_formats.code, foreign key); NULL when none was chosen. Set by a signed-in user only; a vendor sees it but cannot change it.",
          "status": "The board column, stored: assigned, brief_sent, in_production, in_review, ready or launched (a dropped assignment keeps its last status and has dropped_at set). Written only through set_status in app/priorities_assign.py; a linked Creative Tracker row can pull it forward (7 = ready, 8 or 9 = launched), never back.",
          "due_date": "When the vendor should be done: the Friday of for_week unless changed; NULL when cleared.", "priority": "'normal' or 'high'; high cards sort first on the board.",
          "position": "Order of the card inside its board column (ascending, fractional so a card can be dropped between two neighbours).",
          "status_changed_at": "When status last changed (backfilled from the stage timestamps for rows older than migration 245).",
          "concept_name": "The concept's own name as the team and the vendor call it (for example Grandfather used to eat well); empty until set. Shown as the title of the card.",
          "concept_kind": "'new' for a new concept or 'iteration' for a variation made from an earlier concept; NULL when not set. The Creative Tracker holds the same fact in its New Concept / Iteration column.",
          "variations": "The variations of the concept as a JSON list of {label, url, concept_id}: label (Original, Hook 2 ...), url (the video or image, usually Google Drive; may be empty) and concept_id (the Creative Tracker Concept ID of that variation; may be empty). Its length is the Variations number on the ticket.",
          "tag_state": "'complete' when every tag of the ticket was given or has been added by the system, 'pending' while a brief is there and tags are still missing (etl/ticket_tags.py fills them), 'failed' when the tagger gave up after three tries.",
          "tagged_at": "When the system added tags from the brief (etl/ticket_tags.py); NULL when it did not.",
          "tag_note": "The tagger's confidence and one-line reason, or why it failed; empty when nothing was auto-tagged."})

rel("public.creative_priorities_assignment_events", group="creative", kind="table",
    purpose="The history of each production assignment on /priorities: who assigned the concept to a vendor, and who moved it between stages (brief sent, creative delivered, ready to launch, launched, dropped) and when.",
    grain="one row per change to an assignment", source="app/priorities_assign.py, written in the same transaction as the change (create, edit, vendor link page); rows before migration 243 rebuilt from the stage timestamps of creative_priorities_assignments",
    refresh="on every change made on /priorities/production or a vendor link page", coverage="every assignment from creation; for assignments older than migration 243 only the first time each stage was reached, with no person except the creator",
    caveats=["actor is NULL for rebuilt history and when sign-in was off; a vendor link page writes vendor:<name>.", "stage_before and stage_after are the stage the assignment itself shows (its links and Ready / Launched / Dropped buttons); a stage reached only because a linked Creative Tracker row moved on is not an event."],
    cols={"id": "Row number (primary key).", "assignment_id": "The assignment that changed (creative_priorities_assignments.id; rows go with the assignment).", "at": "When the change was made.",
          "actor": "Who made it: the signed-in user name, vendor:<name> for a vendor link page, NULL when not known.",
          "kind": "What changed: assigned, brief_added, brief_removed, creative_added, creative_removed, ready, ready_cleared, launched, launched_cleared, dropped, restored, vendor, week or format (migration 243), plus status_changed, due_changed, priority_changed and comment_added (migration 245).",
          "stage_before": "Stage or status the assignment showed before the change (assigned, brief_sent, creative_in, in_production, in_review, ready, launched, dropped); NULL on the first row. Events of kind status_changed use the status values.", "stage_after": "Stage it showed after the change.",
          "detail": "Short plain text about the change, for example 'Madre to Victoria'; empty when the kind says it all."})

rel("public.creative_priorities_assignment_comments", group="creative", kind="table",
    purpose="Comments on a production assignment, the thread between the team and the vendor on the card (including 'Changes requested: ...' when a creative is sent back).",
    grain="one row per comment", source="app/priorities_assign.py, from the Production page (author_kind app) and the vendor link page (author_kind vendor)",
    refresh="on every comment", coverage="from migration 245",
    caveats=["A vendor sees only the comments of their own assignments; the internal note (creative_priorities_assignments.note) is not a comment and is never shown to a vendor."],
    cols={"id": "Row number (primary key).", "assignment_id": "The assignment commented on (creative_priorities_assignments.id; comments go with the assignment).",
          "author_kind": "'app' for a signed-in team member (or an API key), 'vendor' for a comment written on a vendor link page.",
          "author_name": "Display name: the signed-in user (or Team when sign-in is off), or the vendor name.", "body": "The comment text, 1 to 2,000 characters.", "created_at": "When the comment was posted."})

rel("public.creative_priorities_vendor_capacity", group="creative", kind="table",
    purpose="How many concepts a vendor can take per week, the capacity the Production board compares each vendor's open cards with.",
    grain="one row per vendor (vendor is the primary key)", source="seeded by migration 245 at 10 per vendor; edited on the Production page (PUT /api/priorities/vendor-capacity)",
    refresh="on demand", coverage="the nine vendors",
    caveats=["A soft limit only: going over shows a warning and blocks nothing."],
    cols={"vendor": "Vendor, spelled as in creative_priorities_assignments.vendor (primary key).", "per_week": "Concepts the vendor can take in one week (0 to 1,000).", "updated_at": "When the capacity was last changed."})

rel("public.creative_ad_transcripts", group="creative", kind="table",
    purpose="Transcripts of ads taken from the real audio: one row per ad variation (its tracker Concept ID), made for the videos on Production cards that are Ready to launch and shown on the card's ticket under the matching Hook.",
    grain="one row per ad (concept_id is unique)", source="the transcription job (lonvera_creative_key/cycles/c2026-11-13-transcribe-ready/transcribe_ready.py: downloads the video from Google Drive and runs faster-whisper on this Mac)",
    refresh="when the job is run (on demand, or on a schedule once one is set); a transcript is written once per ad and never overwritten", coverage="videos of Ready to launch Production cards from 2026-11-13; images and silent videos have no speech to transcribe (a silent video is stored as [no speech])",
    caveats=["Speech to text as heard: brand and ingredient names can be misspelled (heme iron may come out as Heam iron).", "Audio is cut at 180 seconds.", "assignment_id is the Production card the ad was made for; it is not a foreign key."],
    cols={"id": "Row number (primary key).", "concept_id": "Tracker Concept ID of the ad variation, for example Lonvera-BoneFlex-Video-8.8-hook2; unique.", "assignment_id": "creative_priorities_assignments.id of the Production card the ad belongs to; NULL when unknown. Not a foreign key.",
          "label": "Short name of the variation on the card: Original, Hook 2 ...", "source_url": "Link to the video file the transcript was made from (Google Drive).", "file_id": "The Google Drive file id inside source_url.",
          "language": "Language the speech was recognised as (en).", "duration_s": "Length of the audio in seconds, before the 180 second cut.", "engine": "Software that made it (faster-whisper).", "model": "Model size used (base.en).",
          "transcript": "The words spoken, as heard; [no speech] for a silent video.", "created_at": "When the transcript was stored.", "created_by": "Who or what stored it."})

rel("public.creative_formats", group="creative", kind="table",
    purpose="The creative formats a production assignment can be made in (for example Whiteboard explainer): a short list that Josh picks from when assigning a concept to a vendor. Production side only; not part of the concept taxonomy.",
    grain="one row per format (code is the primary key)", source="seeded by migration 204; edited by hand in the database (no page edits it); read by /api/priorities/assignments (app/priorities_assign.py)",
    refresh="on demand (a format is added or retired by hand)", coverage="six formats, F1 to F6, from the day the migration was deployed",
    caveats=["A format with active = false stays on the assignments that already use it but can no longer be chosen."],
    cols={"code": "Short code (primary key), F1 to F6.", "name": "What the format is, shown in the pick list.", "length": "Typical running time, as text (for example '45 to 90s'); NULL when not set.",
          "sort": "Order in the pick list (ascending).", "active": "True while the format can be chosen on a new assignment."})

# ---- daily story (migration 248)
rel("public.daily_story_metrics", group="story", kind="table",
    purpose="The metric registry of the daily story: every daily metric the story can use, its basis, unit, formula and the analytics view and columns it comes from.",
    grain="one row per metric_key (primary key)", source="etl/daily_story_registry.py (REGISTRY), upserted by every run of etl.daily_story", refresh="nightly, with the daily story job", coverage="every metric the story computes",
    caveats=["basis names what the number is made of (Upstack new customers, Meta-reported, Klaviyo-attributed ...); never mix bases in one ratio."],
    cols={"metric_key": "Short key used inside daily_story_facts.facts (for example nc_roas, cpm, conv).", "sort_order": "Display order in the registry.", "metric_group": "headline | meta | google | orders | klaviyo | amazon | creative | site | placement.",
          "label": "Plain-language name with the acronym spelled out.", "unit": "usd | pct | x | count (pct is stored as a fraction, 0.12 = 12%).", "basis": "What the number is made of and which attribution it follows.",
          "formula": "How it is computed, ratios always from sums.", "source": "analytics view and columns the inputs come from."})

rel("public.daily_story_facts", group="story", kind="table",
    purpose="The facts the daily story is written from: for each Pacific day, the JSON of every metric against its same-weekday baseline, the exact decomposition of new-customer return on ad spend, mix-versus-rate cuts, the live creative mix, top movers, the change ledger and the Meta, Google, email and text, Amazon and order-mix sections.",
    grain="one row per day (primary key)", source="etl/daily_story.py (compute_facts) over the analytics views", refresh="nightly (etl service 13:00 UTC, non-fatal step after Klaviyo); a day can be recomputed with --day",
    coverage="every day from 2026-05-06 (Meta ad-level start) through yesterday; early days have fewer than four baseline weeks (facts.metrics.<key>.n_baseline says how many)",
    caveats=["Baseline = the same weekday in the previous four weeks: the metric table shows its median and its pooled value; the decomposition uses the pooled value so its terms add back exactly.",
             "The headline is NC ROAS = orders_daily new-customer revenue / (Meta + Google spend). Ad-attributed ROAS inside the cuts is a cut for comparing ads, products and structures, never the headline.",
             "The last 3 to 7 loaded days still move (Meta and Upstack restate): a day's facts are recomputed only when the job runs for it, so older rows can differ slightly from today's tables.",
             "coverage = not_ready when a core source (Meta ads, Upstack orders, Google, Upstack Meta attribution) was not loaded for the day: the row then holds only facts.readiness."],
    cols={"day": "Pacific day the facts describe (primary key).", "facts": "JSON: readiness, windows, metrics, decomposition, anomalies, cuts, movers, creative_live, changes, events, control, sections, cannot_see, basis_notes.",
          "version": "Facts schema version (v1).", "coverage": "full | partial | not_ready: whether every core and optional source existed for the day.", "created_at": "When the facts were last computed."})

rel("public.daily_story", group="story", kind="table",
    purpose="The daily story: one very concise explanation (170 words or fewer) of why a day's performance was what it was, built from daily_story_facts only, with confidence labels and the list of what the database cannot show.",
    grain="one row per day (primary key)", source="etl/daily_story_narrative.py: the model claude-sonnet-5-5 when available and every number in its text is found in the facts, else a deterministic template", refresh="nightly with the facts",
    coverage="every day with facts; backfilled days carry the template text unless a model run was requested",
    caveats=["source says llm or template; note explains a fallback (model off, answer rejected for a number not in the facts).", "Wording is 'consistent with': no story proves a cause unless a clean like-for-like control exists."],
    cols={"day": "Pacific day (primary key).", "headline": "The TLDR sentence.", "story": "The story text: TLDR, a few short lines, and a 'Cannot see' line.", "confidence": "JSON: a confidence label per claim (move, drivers, mix_vs_rate, changes, cause).",
          "cannot_see": "JSON list of what the database cannot show for that day.", "model": "Model that wrote the text; NULL for the template.", "source": "llm | template.", "note": "Why the template was used, when it was.", "created_at": "When the story was written."})

rel("public.daily_story_patterns", group="story", kind="table",
    purpose="The pattern memory: for each day, the change types that showed (new ad sets, capped share up, tROAS share up, a large email send, a cluster entering the live mix ...) with a magnitude bucket and what the next 1-3 days did against their same-weekday baselines.",
    grain="one row per (day, change_type); change_type '_all_days' is the base-rate row every day has", source="etl/daily_story_patterns.py from daily_story_facts (facts.events and facts.metrics)", refresh="nightly; the previous 3 days are refreshed as their outcome windows lengthen",
    coverage="from 2026-05-06; the newest days have out_days below 3",
    caveats=["Correlation, not cause: several changes usually land together and consecutive days' outcome windows overlap.", "out_* is the mean over days D+1..D+3 that exist of (value / same-weekday median baseline - 1); d1_* is D+1 alone; both NULL when the metric was not available."],
    cols={"day": "Day the change showed.", "change_type": "Event type (new_adsets, budget_up, capped_share_up, troas_share_up, big_email_send, cluster_entered, google_spend_up, amazon_spend_up ... or _all_days).",
          "magnitude_bucket": "Size bucket of the largest event of that type that day.", "subject": "The campaign, cluster, hook or send the largest event named; empty when none.", "magnitude": "Numeric size of the largest event (share shift, count or ratio).",
          "out_days": "How many of D+1..D+3 had facts (0 to 3).", "out_nc_roas": "Mean change in NC ROAS vs the same-weekday median over D+1..D+3 (fraction).", "out_cpm": "Same for cost per 1,000 impressions.", "out_ctr": "Same for link click-through rate.",
          "out_conv": "Same for conversion rate (new customers / link clicks).", "out_aov": "Same for new-customer average order value.", "d1_nc_roas": "NC ROAS change vs baseline on D+1 only.", "d1_cpm": "CPM change on D+1.", "d1_ctr": "CTR change on D+1.",
          "d1_conv": "Conversion rate change on D+1.", "d1_aov": "AOV change on D+1.", "updated_at": "When the row was last written."})

rel("public.daily_story_pattern_scoreboard", group="story", kind="view",
    purpose="The pattern scoreboard: how often each change type (and size bucket) has preceded a rise or a fall of more than 5% in NC ROAS, CPM, CTR, conversion rate or AOV over the next 1-3 days, next to the same rate over all days, with sample sizes. Only rows with n of 6 or more. Correlation, not cause.",
    grain="one row per (change_type, magnitude_bucket, metric); magnitude_bucket '(any size)' pools the sizes", source="public.daily_story_patterns", refresh="always current: a view that recomputes on every read; the table behind it is rewritten nightly", coverage="from 2026-05-06",
    caveats=["rise_lift = rise_rate minus base_rise_rate: a pattern is only informative when its lift is clearly away from 0 with a decent n.", "Overlapping outcome windows and simultaneous changes mean n overstates the independent evidence.", "The 5% threshold is far inside the daily noise of NC ROAS, so the base rates are not small: read the lift, not the rate."],
    cols={"change_type": "Event type.", "magnitude_bucket": "Size bucket or '(any size)'.", "metric": "nc_roas | cpm | ctr | conv | aov.", "n": "Days with this change type and an outcome.", "rises": "Days whose next 1-3 days averaged more than 5% above baseline.",
          "falls": "Days whose next 1-3 days averaged more than 5% below baseline.", "rise_rate": "rises / n.", "fall_rate": "falls / n.", "base_rise_rate": "Share of all days whose next 1-3 days averaged more than 5% above baseline.",
          "base_fall_rate": "Same for falls.", "rise_lift": "rise_rate minus base_rise_rate.", "fall_lift": "fall_rate minus base_fall_rate.", "median_delta": "Median outcome change vs baseline (fraction).", "mean_delta": "Mean outcome change (fraction).",
          "base_days": "Days behind the base rate.", "label": "Always 'correlation, not cause'."})

rel("public.creative_priorities_vendor_links", group="creative", kind="table",
    purpose="One permanent unguessable link per vendor for the vendor page /priorities/vendor/<token>, where the vendor sees only their own concepts and adds a creative link and a note.",
    grain="one row per vendor (vendor is the primary key)", source="the Production page via /api/priorities/vendor-links (app/priorities_assign.py)",
    refresh="on demand (a link is created on first use and replaced by Reset link)", coverage="from the day the migration was deployed",
    caveats=["The link is the access: the page needs no login. Reset link replaces the token and the old link stops working."],
    cols={"vendor": "Vendor name (primary key), as in creative_priorities_assignments.vendor.", "token": "Unguessable link token (unique).",
          "created_at": "When the link was first created.", "rotated_at": "When the token was last replaced by Reset link; NULL if never."})

rel("public.unmatched_meta", group="rollups", kind="table",
    purpose="Meta families that map to no live tracker family, with the product guessed for them and their windowed and since-launch numbers. Closes the gap between ad_metrics and the account total.",
    grain="one row per Meta family (meta_family_key)", source="etl.build_metrics from meta_ads, ad_daily, meta_ad_lifetime, meta_campaigns, products.meta_regex",
    refresh="nightly: TRUNCATE and rebuild", coverage="12K families (most never delivered since 2026-05-06; spend_launch can still be > 0)",
    caveats=["product_id is a guess: from the family name (product_source 'name'), else the campaign of the family's biggest ad ('campaign'), else the ad set name ('adset'), else 'none' (the 'unassigned' product).",
             "Windows end on the last complete day. spend_all / purchases_all / revenue_all are over the days held in ad_daily (2026-05-06 on)."],
    cols={
        "meta_family_key": "lower(Meta ad family) (primary key).",
        "meta_family": "Display name (the variant with the highest lifetime spend).",
        "product_id": "Guessed products.id.",
        "spend_7d": "Spend over the last 7 days, dollars.",
        "purchases_7d": "Purchases over the last 7 days.",
        "revenue_7d": "Purchase value over the last 7 days, dollars.",
        "spend_30d": "Spend over the last 30 days, dollars.",
        "purchases_30d": "Purchases over the last 30 days.",
        "revenue_30d": "Purchase value over the last 30 days, dollars.",
        "spend_all": "Spend over every day held in ad_daily, dollars.",
        "purchases_all": "Purchases over every day held.",
        "revenue_all": "Purchase value over every day held, dollars.",
        "live": "true when the family spent on either of the last two days.",
        "spend_launch": "All-time lifetime spend (Meta API), dollars.",
        "purchases_launch": "All-time lifetime purchases.",
        "revenue_launch": "All-time lifetime purchase value, dollars.",
        "impressions_launch": "All-time impressions.",
        "clicks_launch": "All-time link clicks.",
        "product_source": "name | campaign | adset | none.",
    })
for _b, _lbl in (("nc", "Upstack new customers"), ("all", "Upstack all orders")):
    rel(f"public.unmatched_meta_upstack_{_b}", group="rollups", kind="table", like="public.unmatched_meta",
        purpose=f"unmatched_meta on the {_lbl} basis (days held = 2026-05-06 on).",
        grain="one row per Meta family", source=f"etl.build_metrics.rebuild_source('{_b}')", refresh="nightly rebuild", coverage="3.2K families",
        caveats=["purchases_* / revenue_* are Upstack's credit on this basis; spend is Meta's."], cols={})

# =====================================================================================================================================
# public: ops
# =====================================================================================================================================

rel("public.alert_state", group="ops", kind="table",
    purpose="De-duplication state of the failure and staleness alerts sent to Slack by etl.alerts: one row per check (a job that failed, a table that went stale, the web health check ...), so the job alerts once when a problem appears, repeats every 24 hours while it lasts, and sends one 'recovered' line when it clears.",
    grain="one row per check id", source="etl.alerts (hourly Railway cron service `alerts`)", refresh="hourly", coverage="from the first run of the alerts service",
    caveats=["resolved_at is NULL while the problem is open; a problem that comes back reopens the same row (first_seen restarts, alert_count restarts).",
             "last_alerted is NULL when the problem was seen but no message could be sent (for example SLACK_ALERT_WEBHOOK_URL is not set yet): the alert goes out on the first run after the webhook works."],
    cols={"check_id": "Stable id of the check, for example job:run_daily, fresh:meta, web:health (primary key).", "first_seen": "When the current problem was first seen.",
          "last_seen": "Last run that still saw the problem (or the run that saw it clear, when resolved).", "last_alerted": "When Slack was last told about it; NULL if never delivered.",
          "resolved_at": "When the problem cleared; NULL while open.", "alert_count": "Messages sent for the current problem.",
          "fingerprint": "Short text that changes when the problem changes shape (for example another feed started failing); a change re-alerts at once.",
          "last_message": "Text of the last alert (what broke, since when, how to fix)."})

rel("public.meta_breakdown_daily", group="meta", kind="table",
    purpose="Meta Ads results split by one breakdown set (age, gender, country, region, dma, placement, device, hour of day, creative asset ...), pulled at AD grain from the Insights API for both ad accounts. Partitioned by month on day.",
    grain="one row per day x ad account x level (ad; campaign or account only for a set Meta refused at ad level) x entity x breakdown set x dimension values",
    source="Meta Marketing API Insights (read only), etl.meta_breakdowns", refresh="every 2 hours by the dedicated `breakdowns` service: the backfill (newest 30-day window first) until finished, then the trailing 7 days once a day",
    coverage="see meta_breakdown_coverage for the first and last day and the level per set",
    caveats=["purchases and purchase_value are Meta-reported (they count returning customers, not Upstack new customers).",
             "Reach and unique outbound clicks are per row (one ad, one day, one dimension value): never sum them across days, ads or sets.",
             "Action breakdown sets (action_device, action_destination ...) split only purchases and purchase_value; their spend, impressions and click columns are NULL.",
             "Rows of different breakdown sets are different cuts of the same spend: never add across sets.",
             "Meta can return a row for a day with purchases but no spend (view-through), so keep rows with zero spend.",
             "Up to three dimension values per row sit in dim1, dim2, dim3 in the order of the names in breakdown_set (age,gender: dim1 = age, dim2 = gender); asset breakdowns store the JSON text Meta returned."],
    cols={"day": "Calendar day as returned by Meta (each ad account buckets days in its own time zone: Lonvera Main Pacific, Lonvera BackUp Eastern).",
          "account_id": "Meta ad account id with the act_ prefix.",
          "level": "Grain of the entity: ad (the default for every set), campaign or account (only for a set Meta refused at the finer level).",
          "entity_id": "Ad id, campaign id or ad account id, depending on level.",
          "campaign_id": "Campaign of the entity (NULL at account level).",
          "adset_id": "Ad set of the entity (ad level only).",
          "breakdown_set": "Comma-separated breakdown names that were requested together, for example age,gender or publisher_platform,platform_position,impression_device.",
          "dim1": "Value of the first name in breakdown_set (text).", "dim2": "Value of the second name in breakdown_set, NULL when the set has one name.",
          "dim3": "Value of the third name in breakdown_set, NULL when the set has fewer than three.",
          "dims_key": "Stable text form of the dimension values (names sorted, name=value joined with a vertical bar, percent-escaped); any length. Not indexed: the primary key uses dims_hash.",
          "dims_hash": "md5 of dims_key (generated column), the fixed-size stand-in for dims_key in the primary key so very long values (ad copy assets) can be indexed.",
          "spend": "Meta spend in account currency (US dollars). NULL for action breakdown sets.",
          "impressions": "Meta impressions. NULL for action breakdown sets.",
          "reach": "Accounts reached on that day within the row. Not additive across days, ads or sets. NULL for action breakdown sets or when Meta would not return it with the breakdown.",
          "clicks": "All clicks (Meta clicks field), not only link clicks. NULL for action breakdown sets.",
          "inline_link_clicks": "Link clicks (Meta inline_link_clicks). NULL for action breakdown sets.",
          "outbound_clicks": "Outbound clicks (clicks that left Meta; Meta outbound_clicks, action type outbound_click). NULL for action breakdown sets.",
          "unique_outbound_clicks": "Unique outbound clicks within the row (Meta unique_outbound_clicks). Not additive across days or ads. NULL for action breakdown sets.",
          "purchases": "Meta-reported purchases (omni_purchase, else purchase, else pixel purchase), same rule as ad_daily.",
          "purchase_value": "Meta-reported purchase value (same action type rule), gross dollars.",
          "add_to_cart": "Meta-reported adds to cart (omni_add_to_cart, else add_to_cart, else pixel add to cart, same rule as ad_daily). NULL for action breakdown sets and for rows not filled yet (never 0); history is filled by etl.meta_breakdowns --fill-funnel.",
          "initiate_checkout": "Meta-reported initiated checkouts (omni_initiated_checkout, else initiate_checkout, else pixel). NULL for action breakdown sets and for rows not filled yet.",
          "pulled_at": "When this row was last written by the pull."})

rel("public.meta_breakdown_coverage", group="meta", kind="table",
    purpose="What the breakdown pull holds per breakdown set and level: whether Meta accepted the request, the first and last day held, the row count and the last error.",
    grain="one row per breakdown set x level", source="etl.meta_breakdowns (written after every window)", refresh="nightly and after every backfill window", coverage="from the first run",
    caveats=["A set Meta refuses at ad level gets a status unsupported row at level ad (with the reason in last_error) and is pulled at campaign, then account level; each level has its own row.",
             "status unsupported is asked again every 14 days. status error means a transient or unexpected failure, so the set may have gaps.",
             "first_day, last_day and rows are read from meta_breakdown_daily after each window, so during a backfill first_day moves back window by window."],
    cols={"breakdown_set": "Breakdown set name, as in meta_breakdown_daily.breakdown_set.",
          "level": "Level the set was requested at: ad, campaign or account.",
          "status": "ok = Meta accepted it and rows are held; unsupported = Meta refused the request at this level; error = failed for another reason (see last_error).",
          "first_day": "Earliest day held in meta_breakdown_daily for this set and level.",
          "last_day": "Latest day held in meta_breakdown_daily for this set and level.",
          "last_error": "Scrubbed text of the last refusal or failure; NULL when the last check was clean.",
          "rows": "Rows held in meta_breakdown_daily for this set and level.",
          "checked_at": "When the set was last requested."})

rel("public.meta_breakdown_progress", group="meta", kind="table",
    purpose="Resume bookkeeping of the breakdown backfill: one row per finished window of one breakdown set at one level for one ad account, so running the same backfill again skips what is done.",
    grain="one row per breakdown set x level x ad account x window", source="etl.meta_breakdowns (backfill and --auto mode; the plain trailing-days pull does not write it)", refresh="written as each window x set finishes", coverage="backfill windows and the three bookkeeping rows",
    caveats=["A window is skipped on a rerun when a recorded window covers it. The backfill anchor is stored here on the first --auto run, so a resume after midnight needs no arguments.",
             "Three bookkeeping rows have breakdown_set __anchor__ (window_start = oldest day, window_end = the backfill's --until day, rows = max days), __backfill_done__ (the backfill finished) and __caught_up__ (window_end = the last day the daily catch-up covered); level and account_id are '-'.",
             "Safe to delete (or run with --restart): the next backfill simply starts over and pulls again (upserts)."],
    cols={"breakdown_set": "Breakdown set name.", "level": "Level the window was pulled at.", "account_id": "Meta ad account id.", "window_start": "First day of the finished window (the oldest day for the __anchor__ row).",
          "window_end": "Last day of the finished window.", "rows": "Rows written for the window (both accounts are separate rows).", "done_at": "When the window finished."})

rel("analytics.meta_breakdown_daily", group="analytics", kind="view",
    purpose="Meta results split by thirteen everyday breakdowns (audience segment, age, gender, age x gender, country, region, publisher platform, platform position, impression device, device platform, placement, placement x device, hour of day), one row per day x ad x breakdown x value. Meta-reported numbers, including adds to cart and initiated checkouts (the funnel the why tool decomposes by segment).",
    grain="one row per (day, account, ad_id, breakdown, value); NO aggregation above ad",
    source="public.meta_breakdown_daily (etl.meta_breakdowns, Meta Insights API) joined to the thirteen breakdown names", refresh="nightly, trailing 7 days re-pulled; older windows come from the backfill (newest first)",
    coverage="first and last day per breakdown: min(day) / max(day) over this view (a backfill moves first_day back window by window, up to 365 days; a breakdown Meta only serves for a shorter period stops earlier); ad_id and adset_id are NULL only where Meta refused a breakdown at ad level",
    caveats=["Every one of the ten breakdowns is requested at AD level; ad_id and adset_id are NULL only for a breakdown Meta refused at ad level (then campaign_id is set and ad_id and adset_id are NULL; at account level campaign_id is NULL too). Query this view with ad_id IS NOT NULL (or read public.meta_breakdown_coverage: status and level per breakdown) to see which breakdowns have ad-level rows.",
             "purchases and purchase_value are Meta-reported (count returning customers); they are not Upstack new customers and must not be mixed with them in one ratio.",
             "reach and unique_outbound_clicks are per row (one ad, one day, one value): never sum them across days, ads or values. spend, impressions, link_clicks, purchases and purchase_value are additive within one breakdown.",
             "Different breakdowns are different cuts of the same spend: sum within one breakdown only, never across breakdowns.",
             "value for age_gender is 'age|gender' (for example '25-34|female'); hourly values are Meta's advertiser-time-zone hour buckets ('00:00:00 - 00:59:59').",
             "Meta can return purchases for a day with no spend (view-through): keep those rows.",
             "add_to_carts and initiated_checkouts are NULL (not 0) for rows pulled before 2026-11-13 until etl.meta_breakdowns --fill-funnel has filled them; sum them only over days where they are not NULL. placement values are 'publisher|position', placement_device 'publisher|position|device'.",
             "The why tool reads this view to split a move in CPM, click rate, cost per click, link click to add to cart, add to cart to purchase and cost per purchase into segment mix and within-segment rate shifts (shift-share), against a same-weekday control and the segment's own daily noise."],
    cols={"day": "Calendar day as returned by Meta (Lonvera Main Pacific, Lonvera BackUp Eastern).", "account": "Lonvera Main or Lonvera BackUp.",
          "campaign_id": "Meta campaign id (NULL only for a breakdown held at account level).", "adset_id": "Meta ad set id (NULL only for a breakdown Meta refused at ad level).",
          "ad_id": "Meta ad id (NULL only for a breakdown Meta refused at ad level: campaign or account rows).",
          "breakdown": "audience_segment (from user_segment_key) | age | gender | age_gender | country | region | publisher_platform | platform_position | impression_device | device_platform | placement | placement_device | hourly (advertiser time zone). All are requested at ad level.",
          "value": "The text value of the breakdown ('25-34', 'female', 'US', 'facebook', '25-34|female' for age_gender, an hour bucket for hourly).",
          "spend": "Meta spend, dollars.", "impressions": "Meta impressions.", "reach": "Accounts reached on the day within the row (not additive; NULL when Meta would not return it with the breakdown).",
          "link_clicks": "Meta link clicks (inline_link_clicks).", "unique_outbound_clicks": "Unique outbound clicks within the row (not additive).",
          "purchases": "Meta-reported purchases.", "purchase_value": "Meta-reported purchase value, dollars.",
          "add_to_carts": "Meta-reported adds to cart (omni_add_to_cart, else add_to_cart, else pixel). NULL for rows not filled yet, never 0.",
          "initiated_checkouts": "Meta-reported initiated checkouts (omni_initiated_checkout, else initiate_checkout, else pixel). NULL for rows not filled yet."},
    examples=[("Spend and Meta ROAS by age, last 7 complete days",
               "SELECT value AS age, round(sum(spend), 2) AS spend, sum(purchases) AS purchases, round(sum(purchase_value) / NULLIF(sum(spend), 0), 2) AS meta_roas\n"
               "FROM analytics.meta_breakdown_daily WHERE breakdown = 'age' AND day >= current_date - 7 AND day < current_date GROUP BY 1 ORDER BY 1;"),
              ("Which breakdowns have ad-level rows, and how far back each goes",
               "SELECT breakdown, ad_id IS NOT NULL AS ad_level, min(day) AS first_day, max(day) AS last_day, count(*) AS rows\nFROM analytics.meta_breakdown_daily GROUP BY 1, 2 ORDER BY 1, 2;")])

rel("public.meta_change_log", group="meta", kind="table",
    purpose="The Meta Marketing API activity log of both ad accounts: one row per change event (budget, bid, bid strategy, status, targeting, optimization goal, name, creation, creative, billing), who made it and when.",
    grain="one row per activity-log event (event_key = sha1 of account, event_time, event_type, object_id, extra_data, actor_id: Meta gives no event id)",
    source="Meta Marketing API GET /act_<id>/activities (read only), etl.meta_changes", refresh="nightly in the etl run: the last 3 days re-read (overlap, deduped); history was backfilled week by week",
    coverage="the backfill start is in docs (see analytics.data_coverage / min(event_day)); account BackUp has had no events in the loaded window unless rows say otherwise",
    caveats=["object_type is Meta's old vocabulary: ADGROUP = an ad, CAMPAIGN = an ad SET, CAMPAIGN_GROUP = a campaign, ACCOUNT = the ad account; object_level carries the plain word (ad, adset, campaign, account).",
             "Budgets and bids come as minor units and are stored in dollars (old_amount / new_amount: daily or lifetime budget for the budget events, the bid cap or target for bid strategy events, the charge for billing events). "
             "The ROAS floor comes x10000 and is stored as a ratio (0.90 = a 0.90x floor).",
             "extra_data is often incomplete for bids and caps (a change of a bid on an ad set with no strategy change can be missing): meta_adset_state_daily and analytics.meta_change_ledger are the cross-check, from their first snapshot day on.",
             "Meta writes system events too: first_delivery_event (actor Meta), update_ad_run_status to or from Pending Review / Pending Process, billing charges. Filter on actor_name or event_type for human changes.",
             "Two byte-identical events in the same second collapse to one row. add_images / edit_images keep an empty raw (their extra_data is a signed image URL that expires)."],
    cols={"id": "Surrogate row id.", "event_key": "Stable dedupe key (sha1 of account, event_time, event_type, object_id, extra_data, actor_id).",
          "account": "Lonvera Main or Lonvera BackUp.", "event_time": "When Meta recorded the event (timestamptz).",
          "event_day": "Pacific calendar day of event_time (America/Los_Angeles), the day grain every other Lonvera view uses.",
          "event_type": "Meta's event type, for example update_ad_set_budget, update_ad_set_bid_strategy, update_ad_set_run_status, update_ad_run_status, create_ad, create_ad_set, update_campaign_budget, update_ad_set_target_spec.",
          "translated_event_type": "Meta's human text for the event type ('Ad set budget updated').",
          "object_type": "Meta's raw object type: ADGROUP (ad), CAMPAIGN (ad set), CAMPAIGN_GROUP (campaign), ACCOUNT.",
          "object_level": "ad | adset | campaign | account (plain word for object_type).",
          "object_id": "Id of the changed object (ad, ad set, campaign or ad account id, by object_level).",
          "object_name": "Name of the object at the time of the event.",
          "campaign_id": "Campaign of the object: the object itself for campaign events, extra_data.campaign_id for ad set and ad events (NULL when Meta did not send it).",
          "actor_id": "Meta id of who made the change (0 = Meta itself).", "actor_name": "Name of who made the change ('Meta' for system events).",
          "application_name": "App the change came through when Meta says (NULL otherwise).",
          "old_value": "Text of the old value for scalar changes (status, strategy, name, goal, targeting description); NULL for amounts, which sit in old_amount.",
          "new_value": "Text of the new value (same rules as old_value).",
          "old_amount": "Old daily / lifetime budget or bid, dollars (budget and bid strategy events); NULL otherwise or when Meta did not send it.",
          "new_amount": "New daily / lifetime budget or bid, dollars; for create_ad_set / create_campaign_group the starting budget; for billing events the amount charged.",
          "old_roas_floor": "Old ROAS floor as a ratio (bid strategy events only).", "new_roas_floor": "New ROAS floor as a ratio (bid strategy events only).",
          "raw": "The event's extra_data as Meta returned it, parsed to JSON ({} when none; {\"_unparsed\": text} when it was not JSON): the source of truth when a parsed column is NULL. Every other field of the event is a column.",
          "loaded_at": "When the row was first written."})

rel("public.meta_adset_state_daily", group="meta", kind="table",
    purpose="A nightly snapshot of every live ad set's settings in both ad accounts (status, budget, bid, ROAS floor, optimization goal, attribution window): the record that lets a change in a bid or budget be seen even when the activity log's extra_data is incomplete.",
    grain="one row per ad set per snapshot day (Pacific)", source="Meta Marketing API GET /act_<id>/adsets (read only), etl.meta_changes",
    refresh="nightly in the etl run (~05:00 Pacific); a same-day rerun replaces that day's rows", coverage="starts on the day the first snapshot ran (see min(day)); nothing before it exists and it cannot be backfilled",
    caveats=["Live ad sets (any status except archived) every day; archived ad sets only on the days within 3 days of their last update, so the archive step is visible without 4,000 dead rows a day.",
             "Under campaign budget optimization (CBO) the ad set has no budget of its own: daily_budget and lifetime_budget are NULL and the budget lives on the campaign (see meta_change_log update_campaign_budget).",
             "bid_strategy is NULL on ad sets that inherit the strategy from a CBO campaign.",
             "A missing day (the run did not happen) shows as a longer gap in analytics.meta_change_ledger (prev_day), never as a fake change."],
    cols={"adset_id": "Meta ad set id.", "day": "Pacific day of the snapshot.", "account": "Lonvera Main or Lonvera BackUp.", "campaign_id": "Meta campaign id.", "adset_name": "Ad set name on the snapshot day.",
          "status": "Configured status: ACTIVE | PAUSED | ARCHIVED | DELETED.",
          "effective_status": "Effective delivery status (ACTIVE, PAUSED, CAMPAIGN_PAUSED, ARCHIVED, IN_PROCESS, WITH_ISSUES): ACTIVE only when the ad set and its campaign are both on.",
          "daily_budget": "Ad set daily budget, dollars (NULL under CBO).", "lifetime_budget": "Ad set lifetime budget, dollars (NULL when not set).",
          "bid_strategy": "LOWEST_COST_WITHOUT_CAP | COST_CAP | LOWEST_COST_WITH_BID_CAP | LOWEST_COST_WITH_MIN_ROAS ... (NULL when inherited from the campaign).",
          "bid_amount": "Bid cap or cost-cap target, dollars (NULL when none).", "roas_average_floor": "ROAS floor as a ratio (0.90 = 0.90x; Meta stores it x10000); NULL when none.",
          "optimization_goal": "Meta optimization goal (OFFSITE_CONVERSIONS, VALUE, LINK_CLICKS ...).", "attribution_spec": "Attribution windows as Meta returns them, for example [{\"event_type\": \"CLICK_THROUGH\", \"window_days\": 1}].",
          "created_time": "When the ad set was created.", "updated_time": "When Meta last recorded an edit of the ad set (any field).",
          "snapshot_at": "When the row was written."})

rel("analytics.meta_change_log", group="analytics", kind="view",
    purpose="Meta's own activity log of both ad accounts: every budget, bid, strategy, status, targeting, creation and creative change with when it happened and whether Meta itself or a person / app made it. The first place to look when a day's spend, CPM or ROAS moved.",
    grain="one row per activity-log event", source="public.meta_change_log (etl.meta_changes, Meta Marketing API activity log)", refresh="nightly, last 3 days re-read",
    coverage="from the backfill start to yesterday: min(event_day) / max(event_day) over this view",
    caveats=["Most rows are noise for a performance story (first_delivery_event, run-status flips through Pending Review / Pending Process, billing charges, add_images / edit_images): filter is_system_event = false and object_level / event_type, for example update_ad_set_budget, update_ad_set_bid_strategy, update_campaign_budget, update_ad_set_run_status, update_ad_set_target_spec, create_ad_set.",
             "object_level 'adset' is what Meta's raw object_type calls CAMPAIGN; 'campaign' is CAMPAIGN_GROUP. Join object_id to meta_adset_daily.adset_id / meta_campaign_daily.campaign_id.",
             "old_amount / new_amount are dollars (budget and bid events); old_roas_floor / new_roas_floor are ratios. extra_data is often incomplete for bids: cross-check analytics.meta_change_ledger.",
             "event_day is the Pacific day of the event; Meta's own account time zones differ (Main Pacific, BackUp Eastern), so an event near midnight can sit one day off from that account's insight days.",
             "A change is an event, not a cause: this view says what was changed, not what it did to performance."],
    cols={"event_time": "When Meta recorded the event.", "event_day": "Pacific day of the event.", "account": "Lonvera Main or Lonvera BackUp.", "event_type": "Meta event type (update_ad_set_budget ...).",
          "translated_event_type": "Meta's readable name of the event type.", "object_level": "ad | adset | campaign | account.", "object_type": "Meta's raw object type (ADGROUP = ad, CAMPAIGN = ad set, CAMPAIGN_GROUP = campaign).",
          "object_id": "Id of the changed object.", "object_name": "Name of the object at the time.", "campaign_id": "Campaign of the object when known.",
          "is_system_event": "true when Meta itself made the event (first delivery, review status flips, billing), false when a person or an app did; the person's name is not exposed in analytics.", "application_name": "App the change came through, when Meta says.",
          "old_value": "Old value as text (status, strategy, name, targeting description); NULL for amounts.", "new_value": "New value as text.",
          "old_amount": "Old budget or bid, dollars.", "new_amount": "New budget or bid, dollars (starting budget on create events; the amount on billing events).",
          "old_roas_floor": "Old ROAS floor, ratio.", "new_roas_floor": "New ROAS floor, ratio."},
    examples=[("Budget, bid, strategy and on / off changes by day, last 14 days",
               "SELECT event_day, event_type, object_level, count(*) AS events, count(DISTINCT object_id) AS objects\nFROM analytics.meta_change_log\n"
               "WHERE event_day >= current_date - 14 AND event_type IN ('update_ad_set_budget', 'update_ad_set_bid_strategy', 'update_campaign_budget', 'update_ad_set_run_status', 'update_campaign_run_status')\n"
               "GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC;"),
              ("Ad set budget changes with old and new dollars, yesterday",
               "SELECT event_time, object_name, old_amount, new_amount, is_system_event\nFROM analytics.meta_change_log\nWHERE event_type = 'update_ad_set_budget' AND event_day = current_date - 1\nORDER BY event_time;")])

rel("analytics.meta_adset_state_daily", group="analytics", kind="view",
    purpose="Daily snapshot of every live ad set's settings (status, budget, bid, ROAS floor, optimization goal, attribution): the state of the account on each morning, the base of analytics.meta_change_ledger.",
    grain="one row per (day, adset_id)", source="public.meta_adset_state_daily (etl.meta_changes, Meta Marketing API adsets, read only)", refresh="nightly (~05:00 Pacific)",
    coverage="starts on the first snapshot day (min(day) over this view); it is not backfillable",
    caveats=["Taken in the early morning Pacific: a row for day D is the state at the start of D, so changes made on D-1 are already in it.",
             "daily_budget is NULL under campaign budget optimization (the campaign owns the budget); bid_strategy is NULL when inherited.",
             "Archived ad sets appear only for 3 days after their last update; an ad set missing from a day is archived, deleted or not pulled."],
    cols={"day": "Pacific snapshot day.", "adset_id": "Meta ad set id.", "adset_name": "Ad set name that day.", "campaign_id": "Meta campaign id.", "account": "Lonvera Main or Lonvera BackUp.",
          "status": "ACTIVE | PAUSED | ARCHIVED | DELETED (configured).", "effective_status": "ACTIVE only when ad set and campaign are on; else PAUSED, CAMPAIGN_PAUSED, ARCHIVED, IN_PROCESS, WITH_ISSUES.",
          "daily_budget": "Daily budget, dollars (NULL under CBO).", "lifetime_budget": "Lifetime budget, dollars.", "bid_strategy": "Bid strategy (COST_CAP, LOWEST_COST_WITHOUT_CAP ...).",
          "bid_amount": "Bid cap or cost-cap target, dollars.", "roas_average_floor": "ROAS floor as a ratio.", "optimization_goal": "Meta optimization goal.", "attribution_spec": "Attribution windows (JSON).",
          "created_time": "Ad set creation time.", "updated_time": "Meta's last-edit time of the ad set."},
    examples=[("Live ad sets today by strategy, with total daily budget",
               "SELECT coalesce(bid_strategy, '(campaign level)') AS strategy, count(*) AS adsets, sum(daily_budget) AS daily_budget\nFROM analytics.meta_adset_state_daily\n"
               "WHERE day = (SELECT max(day) FROM analytics.meta_adset_state_daily) AND effective_status = 'ACTIVE'\nGROUP BY 1 ORDER BY 2 DESC;"),
              ("One ad set's settings day by day (replace the id)",
               "SELECT day, effective_status, daily_budget, bid_strategy, bid_amount, roas_average_floor\nFROM analytics.meta_adset_state_daily\nWHERE adset_id = '120257382563100538' ORDER BY day;")])

rel("analytics.meta_change_ledger", group="analytics", kind="view",
    purpose="Ad set changes found by comparing each snapshot day with the previous one: new ad sets, paused / activated / archived, budget, bid, strategy, ROAS floor, optimization goal and attribution changes. Independent of the activity log, so it catches bid changes the log's extra_data omits.",
    grain="one row per (day, adset_id, change_type)", source="analytics.meta_adset_state_daily (lag over consecutive snapshot days)", refresh="nightly with the snapshot",
    coverage="starts on the SECOND snapshot day (min(day) over this view); a change is only visible once two consecutive snapshots exist",
    caveats=["A change happened between prev_day's snapshot and day's snapshot: the snapshot runs ~05:00 Pacific, so a change made on Pacific day D shows on day D+1 with prev_day D. A gap in prev_day means a missed snapshot, not a quiet period.",
             "Only ad sets in the snapshot are compared. new_adset needs a creation time inside the gap, so old archived ad sets that are touched again do not show as new. Campaign budgets (CBO) are not in this view: use analytics.meta_change_log update_campaign_budget.",
             "Several changes of the same ad set in one night collapse to the net difference; the sequence is in meta_change_log.",
             "old_number / new_number are dollars for daily_budget, lifetime_budget and bid_amount, a ratio for roas_floor; NULL for text changes."],
    cols={"day": "Snapshot day on which the change is seen.", "prev_day": "Previous snapshot day of this ad set (the change happened after it).", "adset_id": "Meta ad set id.", "adset_name": "Ad set name on day.",
          "campaign_id": "Meta campaign id.", "account": "Lonvera Main or Lonvera BackUp.",
          "change_type": "new_adset | paused (ACTIVE -> PAUSED) | activated (PAUSED -> ACTIVE) | archived | effective_status | daily_budget | lifetime_budget | bid_amount | bid_strategy | roas_floor | optimization_goal | attribution_spec.",
          "old_value": "Old value as text (NULL for new_adset).", "new_value": "New value as text (for new_adset: its effective status).", "old_number": "Old amount (dollars) or floor (ratio) for numeric changes.", "new_number": "New amount or floor."},
    examples=[("Bid and budget changes of the last 7 snapshot days",
               "SELECT day, change_type, count(*) AS adsets\nFROM analytics.meta_change_ledger\nWHERE day > current_date - 7 AND change_type IN ('daily_budget', 'bid_amount', 'bid_strategy', 'roas_floor')\nGROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC;"),
              ("Every change to the ad sets of one campaign (replace the id)",
               "SELECT day, adset_name, change_type, old_value, new_value\nFROM analytics.meta_change_ledger\nWHERE campaign_id = '120257294813250538' ORDER BY day DESC, adset_name;")])

rel("public.etl_runs", group="ops", kind="table",
    purpose="Log of every pipeline job run. analytics.etl_runs is the same table with the error cut to 500 characters.",
    grain="one row per job run", source="etl.db.JobRun (own autocommit connection, so a failed job still leaves a 'failed' row)", refresh="one row per run",
    coverage="since 2026-11-04 UTC",
    cols={
        "id": "Run id.",
        "job": "Job name (run_daily, tracker_sync, meta_pull, lifetime_pull, status_refresh, build_metrics, upstack_daily, load_tags, load_hooks ...).",
        "started_at": "Start time.",
        "finished_at": "Finish time (NULL while running).",
        "status": "running | ok | failed.",
        "data_through": "Last data day covered, when the job has one.",
        "rows_written": "Rows written, when counted.",
        "stats": "Job-specific JSON.",
        "error": "Scrubbed error text when failed.",
    })

rel("public.app_meta", group="ops", kind="table",
    purpose="Key / value facts about the data: window starts, data-through dates, last refresh status, timezone note, tagging reliability.",
    grain="one row per key", source="written by the ETL jobs (etl.db.set_meta)", refresh="updated by the jobs that own each key", coverage="23 keys",
    caveats=["Keys include daily_start (2026-05-06, never moves), data_through, lifetime_cutoff (legacy 2026-09-17), lifetime_pulled_at, metrics_built_at, status_refreshed_at, "
             "upstack_first_day / upstack_through / upstack_built_at / upstack_pulled_at, google_first_day / google_through / google_upstack_first_day / google_built_at, "
             "last_ok_at / refresh_status / last_error, tz_note, tracker_row_count / tracker_blank_family_count, and reliability:<product slug> (blind re-tag agreement as JSON)."],
    cols={
        "key": "Key (primary key).",
        "value": "Value as text (some are JSON).",
        "updated_at": "When the key was last written.",
    })

# =====================================================================================================================================
# public: projection pacing feeds (proj_feed_* = the table the Projections app reads; proj_feedx_* = shadow twin written by the `feeds` service)
# =====================================================================================================================================

_FEED_REFRESH = ("nightly (etl service 13:00 UTC via etl.projection_daily): the trailing 14 days are replaced from the sheet tab; "
                 "the `feeds` service (21:00 UTC) writes the shadow twin proj_feedx_* from the source API and compares")
_SHEET = "the Lonvera Projections Google Sheet tab '{}' (read by etl.projection_pacing)"

rel("public.proj_feed_config", group="proj_feeds", kind="table",
    purpose="Per-feed mode switch for the dual-run: which source the app reads for each feed.",
    grain="one row per feed", source="python -m etl.feeds mode <feed> <mode>", refresh="on change", coverage="14 feeds, all 'shadow' on 2026-11-04",
    caveats=["'sheet' = the sheet raw tabs feed the proj_feed_* table (today's behaviour); 'shadow' = the sheet stays authoritative, the direct pull is written to proj_feedx_* "
             "and compared; 'direct' = the direct pull writes the main proj_feed_* table, the sheet read is the fallback. No row = 'sheet'."],
    cols={"feed": "Feed key (orders, amazon, platform, attribution, traffic, meta_reach, product_reach, nc_product, anyclick, bids, units_revamp, units_amazon, units_intl, stock_revamp).",
          "mode": "'sheet' | 'shadow' | 'direct'.", "changed_at": "When the mode was set.", "changed_by": "Who set it.", "note": "Free note (<= 300 characters)."})

rel("public.proj_feed_runs", group="proj_feeds", kind="table",
    purpose="One row per pacing feed: what was last loaded and through which day (feeds the freshness chips).",
    grain="one row per feed", source="etl.projection_pacing", refresh="nightly", coverage="12 feeds",
    cols={"feed": "Feed key (primary key).", "source": "'sheet:<tab>' | 'ad_daily' | 'snapshot'.", "loaded_at": "When last loaded.",
          "rows_loaded": "Rows of the last load.", "first_day": "First day loaded.", "last_day": "Last day loaded.", "note": "Free note."})

rel("public.proj_feed_attempts", group="proj_feeds", kind="table",
    purpose="One row per direct pull attempt of a feed (any mode).", grain="one row per attempt", source="etl.feeds run",
    refresh="each feeds run (21:00 UTC)", coverage="since 2026-11-04",
    cols={"id": "Primary key.", "feed": "Feed key.", "mode": "Mode at the time.", "source": "'api:upstack' | 'api:warehance' | 'api:meta' | 'api:expandfi' | 'sheet:<tab>'.",
          "started_at": "Start.", "finished_at": "Finish.", "status": "ok | failed | fallback | skipped.", "rows": "Rows pulled.", "first_day": "First day of the pull.",
          "last_day": "Last day of the pull.", "note": "Scrubbed note, <= 300 characters."})

rel("public.proj_feed_verify", group="proj_feeds", kind="table",
    purpose="Latest verification result per feed and day: the shadow table compared with the sheet-fed table.",
    grain="one row per (feed, day)", source="etl.feeds verify", refresh="each feeds run", coverage="trailing window per feed",
    cols={"feed": "Feed key.", "day": "Day compared.", "status": "ok | fail | soft | sheet_stale.", "n_keys": "Keys compared.", "n_bad": "Keys outside tolerance.",
          "worst_col": "Column with the worst difference.", "worst_abs": "Worst absolute difference.", "worst_rel": "Worst relative difference.",
          "detail": "Detail (<= 300 characters).", "checked_at": "When checked."})

rel("public.proj_feed_verify_runs", group="proj_feeds", kind="table",
    purpose="One row per verify run per feed (drives 'N consecutive clean days' before a feed is switched to direct).", grain="one row per run and feed",
    source="etl.feeds verify", refresh="each feeds run", coverage="since 2026-11-04",
    cols={"id": "Primary key.", "at": "When.", "feed": "Feed key.", "window_from": "First day compared.", "window_to": "Last day compared.", "days_checked": "Days compared.",
          "days_failed": "Days failed.", "days_soft": "Days within the soft tolerance.", "days_stale": "Days where the sheet was stale.", "schema_ok": "false when the shadow and main columns differ.",
          "note": "Note (<= 300 characters)."})

rel("public.proj_feed_levers", group="proj_feeds", kind="table",
    purpose="Small numeric levers the feed jobs read at run time (the international-units ratios).", grain="one row per lever", source="python -m etl.feeds lever KEY VALUE (seeded by migration 180)",
    refresh="on change", coverage="4 levers: intl_ratio.bull / liver / boneflex / default",
    cols={"key": "Lever name.", "value": "Value (e.g. 0.905).", "note": "Why / how it was fitted.", "updated_at": "When changed.", "updated_by": "Who changed it."})

rel("public.proj_feed_orders", group="proj_feeds", kind="table",
    purpose="Shopify order totals by Pacific day (not ad-attributed): the sheet-fed twin of upstack_orders_daily with tax and refunds.",
    grain="one row per day", source=_SHEET.format("Orders - History"), refresh=_FEED_REFRESH, coverage="2025-01-01 through yesterday",
    caveats=["Values equal upstack_orders_daily for the shared columns; tax and refunds exist only here. analytics.orders_daily_detail exposes it with the tax and refunds columns; analytics.orders_daily carries the shared columns."],
    cols={"day": "Pacific day.", "nc_rev": "New-customer revenue.", "rc_rev": "Returning-customer revenue.", "nc_orders": "New customers.", "total_rev": "Total revenue.",
          "total_orders": "All orders.", "tax": "Tax collected.", "refunds": "Refunds.", "loaded_at": "When loaded."})
rel("public.proj_feedx_orders", group="proj_feeds", kind="table", like="public.proj_feed_orders",
    purpose="Shadow twin of proj_feed_orders written by the feeds service from the Upstack API.", grain="one row per day", source="etl.feeds (Upstack /api/query)",
    refresh="feeds service 21:00 UTC, trailing window", coverage="trailing window only (about 14 days)", cols={})

rel("public.proj_feed_amazon", group="proj_feeds", kind="table",
    purpose="Amazon.com + B2B sales and Amazon Ads (deduped v3 > v2 > v1) by purchase day.",
    grain="one row per day", source=_SHEET.format("Amazon - Daily") + "; the direct feed reads ExpandFi merchant 1452", refresh=_FEED_REFRESH, coverage="2024-09-24 through yesterday",
    caveats=["Amazon ad spend must be de-duplicated across Amazon's v1 / v2 / v3 report generations (raw is about 1.3x high); this table already is.", "Exposed as analytics.amazon_daily."],
    cols={"day": "Purchase day.", "orders": "Orders.", "units": "Units.", "revenue": "Revenue.", "nc_orders": "New-customer orders.", "nc_rev": "New-customer revenue.",
          "ad_spend": "Amazon Ads spend (deduped).", "attributed_sales": "Sales Amazon attributes to ads.", "clicks": "Ad clicks.", "impressions": "Ad impressions.", "loaded_at": "When loaded."})
rel("public.proj_feedx_amazon", group="proj_feeds", kind="table", like="public.proj_feed_amazon", purpose="Shadow twin of proj_feed_amazon (ExpandFi direct feed).",
    grain="one row per day", source="etl.feeds (ExpandFi)", refresh="feeds service, trailing window", coverage="trailing window only", cols={})

rel("public.proj_feed_platform", group="proj_feeds", kind="table",
    purpose="Ad-platform spend and platform-reported conversions per day for Meta and Google (Upstack ad-platform feed): the longest Meta + Google spend history in the database.",
    grain="one row per (day, platform)", source=_SHEET.format("Platform - History") + "; the direct feed reads Upstack /api/query-channel-details at platform grain", refresh=_FEED_REFRESH,
    coverage="2025-01-01 through yesterday; platform values 'Meta (Facebook/Instagram)' and 'Google Ads'",
    caveats=["platform labels differ from analytics.platform_daily ('meta' / 'google'). Spend reconciles with ad_daily and google_daily to the cent on every day they share.",
             "Purchases and values are platform-reported and overlap across platforms."],
    cols={"day": "Day.", "platform": "'Meta (Facebook/Instagram)' | 'Google Ads' (TikTok has no rows).", "spend": "Spend, dollars.", "purchases": "Platform-reported purchases.",
          "purchase_value": "Platform-reported conversion value.", "nc_purchases": "Platform-reported new-customer purchases (0 for Google).", "nc_purchase_value": "Their value.",
          "link_clicks": "Link clicks.", "clicks": "All clicks.", "loaded_at": "When loaded."})
rel("public.proj_feedx_platform", group="proj_feeds", kind="table", like="public.proj_feed_platform", purpose="Shadow twin of proj_feed_platform (Upstack direct feed).",
    grain="one row per (day, platform)", source="etl.feeds (Upstack)", refresh="feeds service, trailing window", coverage="trailing window only", cols={})

rel("public.proj_feed_attribution", group="proj_feeds", kind="table",
    purpose="New customers and orders by ad source (Meta Ads, Google Ads, ...) per day, last-click attribution.",
    grain="one row per (day, ad_source)", source=_SHEET.format("Attribution - History"), refresh=_FEED_REFRESH, coverage="2025-01-01 through yesterday",
    caveats=["Last-click (exclusive per order), unlike the any-click 30-day attribution in the upstack_* tables: the two do not agree."],
    cols={"day": "Pacific day.", "ad_source": "Ad source label: 'Meta Ads' | 'Google Ads' | ...", "nc_orders": "New customers credited.", "nc_rev": "Their revenue.", "orders": "All orders credited.",
          "rev": "Their revenue.", "loaded_at": "When loaded."})
rel("public.proj_feedx_attribution", group="proj_feeds", kind="table", like="public.proj_feed_attribution", purpose="Shadow twin of proj_feed_attribution.",
    grain="one row per (day, ad_source)", source="etl.feeds (Upstack)", refresh="feeds service, trailing window", coverage="trailing window only", cols={})

rel("public.proj_feed_traffic", group="proj_feeds", kind="table",
    purpose="Pixel-tracked visitors per day (unique per day, not additive across periods).", grain="one row per day", source=_SHEET.format("Traffic - Daily"),
    refresh=_FEED_REFRESH, coverage="2025-05-08 through today", caveats=["visitors are unique within a day: do not sum days to get unique visitors."],
    cols={"day": "Day.", "new_visitors": "First-time visitors.", "visitors": "Visitors.", "sessions": "Sessions.", "loaded_at": "When loaded."})
rel("public.proj_feedx_traffic", group="proj_feeds", kind="table", like="public.proj_feed_traffic", purpose="Shadow twin of proj_feed_traffic.",
    grain="one row per day", source="etl.feeds (Upstack)", refresh="feeds service, trailing window", coverage="trailing window only", cols={})

rel("public.proj_feed_meta_reach", group="proj_feeds", kind="table",
    purpose="Account-level Meta impressions, reach and unique outbound clicks pulled AT EACH GRAIN (reach is not additive), both ad accounts summed. Behind analytics.meta_reach.",
    grain="one row per (grain, period_key)", source=_SHEET.format("Meta Account - Reach"), refresh=_FEED_REFRESH,
    coverage="day 2025-01-01.., week 2024-12-30.., month 2025-01-01..; current period is partial",
    cols={"grain": "'day' | 'week' | 'month'.", "period_key": "'2026-11-02' | '2026-W45' | '2026-11'.", "period_start": "First day.", "period_end": "Last day.",
          "impressions": "Impressions.", "reach": "People reached (de-duplicated by Meta).", "unique_link_clicks": "Unique link clicks.", "link_clicks": "Link clicks.",
          "unique_outbound_clicks": "Unique outbound clicks.", "loaded_at": "When loaded."})
rel("public.proj_feedx_meta_reach", group="proj_feeds", kind="table", like="public.proj_feed_meta_reach", purpose="Shadow twin of proj_feed_meta_reach written from the Meta Graph API by the feeds service.",
    grain="one row per (grain, period_key)", source="etl.feeds (Meta Graph API insights)", refresh="feeds service, trailing window", coverage="trailing window only (from 2026-09-28)", cols={})

rel("public.proj_feed_product_reach", group="proj_feeds", kind="table",
    purpose="Unique outbound clicks per product, de-duplicated across the product's ad sets, per grain. The only per-product reach-like metric.",
    grain="one row per (grain, period_key, product)", source=_SHEET.format("Meta Product - Reach"), refresh=_FEED_REFRESH,
    coverage="from 2026-07-05 (Meta serves filtered unique clicks about 120 days back); earlier periods are absent, never re-pulled",
    caveats=["Reach and frequency per product are NOT stored: only unique outbound clicks."],
    cols={"grain": "'day' | 'week' | 'month'.", "period_key": "Period key.", "period_start": "First day.", "period_end": "Last day.", "product": "VitaBull | Beef Liver | Boneflex | Other.",
          "unique_outbound_clicks": "Unique outbound clicks de-duplicated across the product's ad sets.", "loaded_at": "When loaded."})
rel("public.proj_feedx_product_reach", group="proj_feeds", kind="table", like="public.proj_feed_product_reach", purpose="Shadow twin of proj_feed_product_reach.",
    grain="one row per (grain, period_key, product)", source="etl.feeds (Meta Graph API)", refresh="feeds service, trailing window", coverage="trailing window only", cols={})

rel("public.proj_feed_nc_product", group="proj_feeds", kind="table",
    purpose="ACTUAL Shopify first-order revenue and new customers per Pacific day by the order's largest product line (not Meta-attributed). The only product split of actual new-customer revenue.",
    grain="one row per (day, product)", source=_SHEET.format("NC Rev - By Product") + "; the direct feed reads ExpandFi merchant 1452 shopify_orders", refresh=_FEED_REFRESH,
    coverage="2025-01-01 through today; product = VitaBull | Beef Liver | Boneflex | Other",
    caveats=["Exposed as analytics.orders_by_product_daily. Revenue = order total_price split across product lines by net line price; nc_orders = orders whose largest product line is the product."],
    cols={"day": "Pacific day.", "product": "VitaBull | Beef Liver | Boneflex | Other.", "nc_rev": "First-order revenue, dollars.", "nc_orders": "First orders.", "loaded_at": "When loaded."})
rel("public.proj_feedx_nc_product", group="proj_feeds", kind="table", like="public.proj_feed_nc_product", purpose="Shadow twin of proj_feed_nc_product (ExpandFi direct feed).",
    grain="one row per (day, product)", source="etl.feeds (ExpandFi)", refresh="feeds service, trailing window", coverage="trailing window only", cols={})

rel("public.proj_feed_anyclick", group="proj_feeds", kind="table",
    purpose="Upstack any-click (30-day) new customers on Scaling and Testing campaigns per day.", grain="one row per (day, stage)", source=_SHEET.format("Upstack Any-Click - By Campaign"),
    refresh=_FEED_REFRESH, coverage="2025-05-08 through yesterday",
    cols={"day": "Day.", "stage": "'Scaling' | 'Testing'.", "nc": "New customers credited.", "nc_rev": "Their first-order revenue.", "loaded_at": "When loaded."})
rel("public.proj_feedx_anyclick", group="proj_feeds", kind="table", like="public.proj_feed_anyclick", purpose="Shadow twin of proj_feed_anyclick.",
    grain="one row per (day, stage)", source="etl.feeds (Upstack)", refresh="feeds service, trailing window", coverage="trailing window only", cols={})

rel("public.proj_feed_bids", group="proj_feeds", kind="table",
    purpose="Meta bid settings per day, summed: spend on capped / floor ad sets and spend-weighted bid sums.", grain="one row per day", source=_SHEET.format("Meta Bids - Daily"),
    refresh=_FEED_REFRESH, coverage="2026-04-28 through today (settings of past days are rebuilt from the account activity log, so older history cannot be re-pulled)",
    cols={"day": "Day.", "spend": "Total spend.", "capped_spend": "Spend on ad sets with a cost cap.", "spend_x_cap": "Sum of spend x cap.", "floor_spend": "Spend on ad sets with a ROAS floor.",
          "spend_x_floor": "Sum of spend x floor.", "loaded_at": "When loaded."})
rel("public.proj_feedx_bids", group="proj_feeds", kind="table", like="public.proj_feed_bids", purpose="Shadow twin of proj_feed_bids.", grain="one row per day",
    source="etl.feeds (Meta Graph API)", refresh="feeds service, trailing window", coverage="trailing window only", cols={})

rel("public.proj_feed_meta_day", group="proj_feeds", kind="table",
    purpose="Meta by campaign and day for the Pacing module. 'app' rows are aggregated from ad_daily; 'sheet' rows are the sheet's 'Meta Campaign - History' for days before ad_daily begins. Behind analytics.meta_campaign_daily_history.",
    grain="one row per (day, campaign_id, source)", source="app rows: ad_daily (etl.projection_pacing); sheet rows: the sheet tab 'Meta Campaign - History' (backfilled once)",
    refresh="nightly (app rows, trailing 14 days)", coverage="sheet 2024-12-31 through 2026-11-03; app 2026-05-06 onward",
    caveats=["A day is read from 'app' when it has app rows, else from 'sheet' (the view does this). product and stage are classified from the campaign name."],
    cols={"day": "Day.", "campaign_id": "Meta campaign id.", "source": "'app' | 'sheet'.", "campaign_name": "Campaign name.", "product": "VitaBull | Beef Liver | Boneflex | Unclassified (from the name).",
          "stage": "'Testing' when the name contains 'test', else 'Scaling'.", "spend": "Spend.", "impressions": "Impressions.", "link_clicks": "Link clicks.", "purchases": "Meta-reported purchases.",
          "purchase_value": "Meta-reported purchase value.", "loaded_at": "When loaded."})

rel("public.proj_feed_meta_product", group="proj_feeds", kind="table",
    purpose="Meta spend and link clicks per day by product, classified campaign name -> ad set name -> ad name -> Other (the sheet's 'Meta Spend - By Product' rule).",
    grain="one row per (day, product, source)", source="'app' from ad_daily; 'sheet' for older days", refresh="nightly", coverage="2025-01-01 through yesterday",
    cols={"day": "Day.", "product": "VitaBull | Beef Liver | Boneflex | Other.", "source": "'app' | 'sheet'.", "spend": "Spend.", "link_clicks": "Link clicks.", "loaded_at": "When loaded."})

rel("public.proj_pacing_hourly", group="proj_feeds", kind="table", purpose="Meta time-of-day spend curve (hourly spend of the last 30 days): typed input for the intraday projection.",
    grain="one row per hour of day (Pacific)", source="typed from Meta Ads Manager's time-of-day report", refresh="manual", coverage="24 rows",
    cols={"hour": "Hour of day, Pacific (0-23).", "spend_30d": "Spend in that hour over the 30 days.", "days_in_curve": "Days in the curve.", "note": "Note.", "updated_at": "When updated."})
rel("public.proj_pacing_setting", group="proj_feeds", kind="table", purpose="Feed classification rules (product patterns, the testing keyword) applied when a day is loaded. Editable through the pacing API.",
    grain="one row per key", source="app settings", refresh="on change", coverage="empty until edited (defaults are the sheet's rules)",
    cols={"key": "Setting key.", "value": "Value (jsonb).", "note": "Note.", "updated_at": "When updated."})

rel("public.proj_units_daily", group="proj_feeds", kind="table",
    purpose="Units shipped or sold per day by location and product (US 3PL, FBA, overseas 3PL). Scenario-independent facts for inventory demand.",
    grain="one row per (location, product, day)", source="Warehance API (revamp), ExpandFi (amazon), the 3PL's tracking exports and an estimate (intl)", refresh="nightly feeds",
    coverage="amazon 2024-09-24.., intl 2026-07-01.., revamp 2026-07-17.. (US shipments exist only from the warehouse go-live)", caveats=["Aggregates only. is_estimate = true rows are modeled from another feed (international: Warehance non-US units x a fitted ratio)."],
    cols={"location": "revamp (US 3PL, Warehance) | amazon (FBA) | intl (overseas 3PL).", "product": "bull | liver | boneflex | belle | flow.", "day": "Day.", "units": "Units.",
          "source": "'warehance' | 'expandfi' | 'tracking-xlsx' | 'estimate' | 'upload'.", "is_estimate": "true = modeled.", "loaded_at": "When loaded."})
rel("public.proj_feedx_units_daily", group="proj_feeds", kind="table", like="public.proj_units_daily", purpose="Shadow twin of proj_units_daily written by the feeds service.",
    grain="one row per (location, product, day)", source="etl.feeds (Warehance / ExpandFi)", refresh="feeds service", coverage="trailing window", cols={})

rel("public.proj_demand_snapshot", group="proj_feeds", kind="table", purpose="Stock on hand and allocated per location and product, refreshed daily (Warehance for the US warehouse).",
    grain="one row per (location, product, as_of)", source="Warehance API / counts", refresh="nightly", coverage="2026-11-03 onward (one snapshot per day)",
    cols={"location": "revamp | amazon | intl.", "product": "Product key.", "as_of": "Snapshot date.", "on_hand": "Units on hand.", "allocated": "Units allocated to open orders.",
          "source": "Where the count came from.", "loaded_at": "When loaded."})
rel("public.proj_feedx_demand_snapshot", group="proj_feeds", kind="table", like="public.proj_demand_snapshot", purpose="Shadow twin of proj_demand_snapshot.",
    grain="one row per (location, product, as_of)", source="etl.feeds (Warehance)", refresh="feeds service", coverage="trailing window", cols={})

rel("public.proj_feedx_intl_nonus_daily", group="proj_feeds", kind="table",
    purpose="Daily units on Warehance orders shipped to a non-US address (any Warehance status: Shopify routes them to the overseas warehouse, Warehance auto-cancels them), by Pacific order date, product and country.",
    grain="one row per (day, product, country)", source="etl.feeds intl_estimate (Warehance API); no sheet tab carries it", refresh="feeds service 21:00 UTC", coverage="about 488 rows",
    caveats=["Raw bottle counts, not yet scaled by the ratio lever. Aggregates only."],
    cols={"day": "Pacific order day.", "product": "Product key.", "country": "ISO 3166 alpha-2, upper case.", "units_raw": "Raw non-US bottles.", "loaded_at": "When loaded."})
rel("public.proj_feed_nc_hourly", group="proj_feeds", kind="table",
    purpose="New-customer revenue and first orders by Pacific day and clock hour (Shopify first orders from the ExpandFi warehouse); the hourly share curve behind the 'NC revenue today' card on Projections > Pacing.",
    grain="one row per (day, hour) with at least one first order", source="etl.feeds nc_hourly (ExpandFi warehouse shopify_orders); no sheet tab carries it", refresh="feeds service 21:00 UTC, trailing 35 closed days",
    coverage="from the first pull (60-day backfill once)",
    caveats=["Sparse: an hour with no first order has no row. Today is never stored. The 25-hour fall-back day merges both 1 a.m. hours into hour 1. Order totals include tax and are before refunds, like upstack_orders_daily.nc_rev; day sums equal proj_feed_nc_product. Aggregates only."],
    cols={"day": "Pacific day of the first order.", "hour": "Pacific wall-clock hour 0-23.", "nc_rev": "Sum of order total_price of the first orders in that hour (tax included, before refunds); test orders and orders without priced lines excluded.",
          "nc_orders": "Number of first orders in that hour.", "loaded_at": "When loaded."})
rel("public.proj_live_today_snapshots", group="proj_feeds", kind="table",
    purpose="Today's Meta spend, Google spend, new-customer revenue and new customers so far, one snapshot per hourly pull from Upstack; fills the 'Meta spend today' and 'NC revenue today' cards on Projections > Pacing.",
    grain="one row per pull (pulled_at, to the minute)", source="etl.live_today (Upstack /api/query and /api/query-channel-details for the Pacific day so far); no sheet tab carries it", refresh="Railway cron `live`, every hour on the hour",
    coverage="from the first pull; history kept 90 days",
    caveats=["Upstack's spend for the current day trails the ad platforms by some minutes, so the figures are a floor of what has been spent and sold. Same measures as upstack_orders_daily (nc_rev = orders.new_customer_total_revenue, tax included, before refunds), so a closed day ties. Aggregates only."],
    cols={"pulled_at": "When Upstack was asked, to the minute (UTC).", "day": "Pacific day the numbers belong to.", "pacific_time": "Pacific wall-clock time of the pull, to the minute.",
          "meta_spend": "Meta (Facebook / Instagram) spend so far that day, dollars, all ad accounts Upstack sees.", "google_spend": "Google Ads spend so far that day, dollars.",
          "total_spend": "Spend so far across every ad platform Upstack reports (Meta, Google, TikTok), dollars.", "nc_rev": "New-customer revenue so far that day, dollars (orders.new_customer_total_revenue).",
          "nc_orders": "New customers (first orders) so far that day."})
rel("public.proj_amazon_mtd_snapshots", group="proj_feeds", kind="table",
    purpose="Completed Amazon month-to-date revenue, one snapshot per Pacific day and month: reported days plus the missing days projected at the trailing 7-day average of the reported days; read by Projections > Pacing instead of computing on every page load.",
    grain="one row per (snapshot_day, month)", source="proj_feed_amazon.revenue through etl.amazon_mtd", refresh="after the Amazon feed load in the feeds service (21:00 UTC) and again in the etl service (13:00 UTC); a same-day re-run replaces the row",
    coverage="from the first run; history kept so each day's projection can be compared with the final month",
    caveats=["projected_mtd is an estimate, never an actual; a later feed load restates days (the page then computes live); Amazon revenue is total_amount_usd by purchase day, Amazon.com plus B2B. Aggregates only."],
    cols={"snapshot_day": "Pacific day of the run.", "month": "First day of the month that was completed.", "computed_at": "When the run computed it (UTC).",
          "data_through": "Last day that should have been complete at the run (yesterday by default).", "last_actual_day": "Last day of the month with reported Amazon revenue; null before the first reported day.",
          "days_actual": "Calendar days from the 1st through the last reported day.", "days_projected": "Days after the last reported day, through data_through, that were projected (0 when the feed is current or stale).",
          "daily_rate": "Dollars per projected day: mean of the last 7 reported days with revenue above zero (fewer when fewer exist).", "actual_mtd": "Reported Amazon revenue from the 1st through the last reported day, dollars.",
          "projected_mtd": "actual_mtd plus the projected days plus any partial-day top-up, dollars.",
          "status": "complete (nothing to project), projected, stale (feed more than 5 days behind: nothing projected), no_rate (no revenue in the feed), no_days (month has no complete day).",
          "method": "Rule and version that made the row, e.g. trailing-7-available/v1.", "flags": "Plain flags: feed_stale, short_window, window_has_gaps, gap_days_inside_month, newest_day_partial, no_data.",
          "projected_days": "The projected days as json: [{day, revenue}], so a reader can rebuild the completed month without recomputing.",
          "partial_topup": "Dollars added because the newest reported day looked partial (below half the days before it); 0 otherwise.", "lag_days": "Days between data_through and the last reported day.",
          "rate_source": "trailing_7_available, or previous_month_last_7 when the month has no reported day yet.", "note": "The plain-language line the page shows."})
rel("public.proj_feedx_intl_estimate", group="proj_feeds", kind="view",
    purpose="The overseas-3PL units estimate: raw non-US units x the product's ratio lever (else intl_ratio.default), flagged estimated.",
    grain="one row per (day, product)", source="proj_feedx_intl_nonus_daily x proj_feed_levers", refresh="live view", coverage="as the raw table",
    caveats=["Half-up rounding at (day, product); the python job rounds half-to-even, so a tie can differ by one unit from proj_feedx_units_daily."],
    cols={"day": "Pacific day.", "product": "Product key.", "units_raw": "Raw non-US units.", "factor": "Ratio applied.", "units": "Estimated overseas-3PL units.", "estimated": "Always true."})

rel("public.proj_meta_spend_monthly", group="proj_feeds", kind="view",
    purpose="Monthly Meta spend from ad_daily, read by the projection drivers module.", grain="one row per month", source="ad_daily", refresh="live view", coverage="2026-05 onward",
    caveats=["A month is complete when days = the days in the month."],
    cols={"month": "First day of the month.", "spend": "Meta spend, dollars.", "days": "Days with any ad_daily row.", "last_day": "Last day with data."})

# =====================================================================================================================================
# public: projection engine (finance, inventory, cohorts). Read through the analytics.projection_* / inventory_* / cohorts_* views (migration 230); the raw tables stay private.
# =====================================================================================================================================

_ENG = dict(group="proj_engine", kind="table", recomment="relation")
_FIN = "Projection engine (finance / planning); read it through the analytics projection_* / inventory_* / cohorts_* views, never the raw table."

rel("public.proj_scenarios", purpose="Named lever sets (scenarios) of the Projections module. " + _FIN, grain="one row per scenario", source="the Projections app",
    refresh="on edit", coverage="7 scenarios incl. the default 'current'", caveats=["params holds the module levers (financing, drivers, cohorts...): finance data."],
    cols={"id": "Primary key.", "slug": "Unique slug.", "name": "Name.", "base_slug": "Fallback scenario for series cells this one does not hold.", "is_default": "true for the plan of record.",
          "params": "params[<module>] = that module's levers (jsonb).", "version": "+1 on every change.", "created_at": "Created.", "updated_at": "Updated.",
          "frozen": "true = a frozen clone that no longer reads its base.", "archived_at": "Set when deleted: kept 30 days."}, **_ENG)
rel("public.proj_series", purpose="Typed monthly cells a scenario changes ('input' replaces a constant, 'override' replaces a computed month). " + _FIN, grain="one row per (scenario, line, month)",
    source="the Projections app", refresh="on edit", coverage="1.6K cells",
    cols={"scenario_id": "proj_scenarios.id.", "line_key": "Series key.", "month": "First of the month.", "value": "Value.", "kind": "input | override.", "note": "Note.", "updated_at": "When changed."}, **_ENG)
rel("public.proj_actuals", purpose="Scenario-independent monthly facts loaded from exports (QuickBooks lines, cohort rollups...). Finance. " + _FIN, grain="one row per (line_key, month)",
    source="QuickBooks uploads and nightly roll-ups", refresh="monthly upload / nightly", coverage="825 cells",
    cols={"line_key": "Line key (e.g. 'pnl.<line>', 'spend.meta_feed').", "month": "First of the month.", "value": "Value.", "source": "Where it came from.", "loaded_at": "When loaded."}, **_ENG)
rel("public.proj_audit", purpose="Audit trail of every change made in the Projections module. " + _FIN, grain="one row per change", source="the Projections app", refresh="on edit", coverage="494 rows",
    cols={"id": "Primary key.", "at": "When.", "actor": "Who.", "scenario_slug": "Scenario (text: the trail outlives a deleted scenario).", "action": "What was done.", "line_key": "Line touched.",
          "month": "Month touched.", "old": "Value before.", "new": "Value after.", "note": "Note.", "version": "Scenario version the change produced."}, **_ENG)
rel("public.proj_scenario_history", purpose="Snapshot (params + series) of a scenario per version, newest 200 kept; used by restore. " + _FIN, grain="one row per (scenario, version)",
    source="the Projections app", refresh="on edit", coverage="293 rows", cols={"scenario_id": "proj_scenarios.id.", "version": "Scenario version.", "snapshot": "Params and series (jsonb).",
    "at": "When.", "action": "What produced it."}, **_ENG)
rel("public.proj_refresh_runs", purpose="One row per module per run of the nightly projection refresh. " + _FIN, grain="one row per module run", source="etl.projection_daily",
    refresh="nightly", coverage="35 rows", cols={"id": "Primary key.", "module": "Projection module.", "started_at": "Start.", "finished_at": "Finish.", "status": "ok | failed | skipped.",
    "rows": "Rows refreshed.", "data_through": "Last data day.", "error": "Scrubbed error (<= 300 characters)."}, **_ENG)

rel("public.proj_cohorts", purpose="Customer cohort table, one row per acquisition month (new customers, first-order $, CAC, repeat rate). Finance / planning inputs. " + _FIN,
    grain="one row per cohort_month", source="cohort export uploads", refresh="monthly upload", coverage="33 cohorts",
    caveats=["Aggregates only. cac is a per-customer rate at full precision."],
    cols={"cohort_month": "First of the acquisition month.", "new_customers": "Customers acquired.", "first_order_per_customer": "Average first-order $ per customer.",
          "first_order_realized": "Same after refunds, reference only.", "repeat_90d": "90-day repeat rate (fraction).", "cac": "Cost to acquire a customer as exported.",
          "payback_months": "Payback period in months.", "nc_rev_reference": "New-customer revenue typed on the sheet, used only to tie the engine out.", "source": "Source label.", "loaded_at": "When loaded."}, **_ENG)
rel("public.proj_cohort_cum", purpose="Cumulative $ per cohort customer at each age (months after acquisition). " + _FIN, grain="one row per (cohort_month, age)", source="cohort export uploads",
    refresh="monthly upload", coverage="522 cells", cols={"cohort_month": "proj_cohorts.cohort_month.", "age": "Months after acquisition (1-36).", "cum_per_customer": "Cumulative $ per customer, first order included."}, **_ENG)
rel("public.proj_cohort_uploads", purpose="One row per cohort upload with the prior state, so the last upload can be undone. " + _FIN, grain="one row per upload", source="the Projections app",
    refresh="on upload", coverage="empty", cols={"id": "Primary key.", "at": "When.", "actor": "Who.", "filename": "File name.", "sha256": "File hash.", "through": "Last month the upload could touch.",
    "summary": "Preview counts (jsonb).", "before": "State of every touched cohort before the upload (jsonb).", "undone_at": "When undone.", "undone_by": "Who undid it."}, **_ENG)
rel("public.proj_curve_goal", purpose="Goal retention curve snapshots (repeat $ per $1 of first-order revenue by age 1..60). " + _FIN, grain="one row per (snapshot_id, age)",
    source="the Projections app", refresh="on save", coverage="60 rows", caveats=["The newest snapshot is the active one."],
    cols={"snapshot_date": "Snapshot date.", "age": "Age in months (1-60).", "value": "Repeat $ per $1 of first-order revenue.", "note": "Note.", "snapshot_id": "Snapshot id.",
          "saved_at": "When saved.", "saved_by": "Who saved.", "restored_from": "snapshot_id this is a copy of."}, **_ENG)

rel("public.proj_pnl_drivers", purpose="How each P&L line is projected (segments of fixed / carry / rate modes). Finance. " + _FIN, grain="one row per line", source="seeded from the sheet",
    refresh="on edit", coverage="32 lines", cols={"line_key": "P&L line key.", "segments": "Ordered segments (jsonb).", "note": "Note.", "source": "Sheet cell the default came from.", "updated_at": "When changed."}, **_ENG)
rel("public.proj_pnl_settings", purpose="P&L engine settings (rate window, inflation, headcount, COGS, EV). Finance. " + _FIN, grain="one row per setting", source="the Projections app",
    refresh="on edit", coverage="6 keys", cols={"key": "Setting name.", "value": "Value (jsonb).", "note": "Note.", "updated_at": "When changed."}, **_ENG)
rel("public.proj_pnl_cogs_sku", purpose="Landed unit cost by SKU and the sales mix that weights it (the forward COGS model). Finance. " + _FIN, grain="one row per SKU", source="typed inputs",
    refresh="on edit", coverage="12 SKUs", cols={"sku": "SKU.", "label": "Label.", "china_unit_cost": "Landed cost per unit from the China vendor.", "us_unit_cost": "Landed cost per unit from the US vendor.",
    "sales_mix": "Share of sales.", "in_stock_qty": "Units in stock.", "sort": "Order.", "note": "Note.", "updated_at": "When changed."}, **_ENG)
rel("public.proj_pnl_qb_map", purpose="QuickBooks account name -> P&L line key map. Finance. " + _FIN, grain="one row per account", source="typed", refresh="on edit", coverage="61 accounts",
    cols={"account": "QuickBooks account (lower-cased).", "line_key": "P&L line key.", "note": "Note.", "updated_at": "When changed."}, **_ENG)
rel("public.proj_pnl_vendors", purpose="Vendor retainers, current vs proposed, monthly. Reference only. Finance. " + _FIN, grain="one row per vendor", source="typed", refresh="on edit", coverage="10 vendors",
    cols={"vendor": "Vendor.", "current_monthly": "Current monthly retainer.", "proposed_monthly": "Proposed monthly retainer.", "sort": "Order.", "note": "Note."}, **_ENG)
rel("public.proj_upload_history", purpose="Every QuickBooks upload commit with what it replaced, so the latest can be undone. Finance. " + _FIN, grain="one row per upload", source="the Projections app",
    refresh="on upload", coverage="empty", cols={"id": "Primary key.", "at": "When.", "actor": "Who.", "filename": "File.", "sha256": "Hash.", "bytes": "Size.", "file_rows": "Rows in file.",
    "months": "Months touched (jsonb).", "new_cells": "Cells created.", "changed_cells": "Cells changed.", "replaced": "Replaced cells (jsonb).", "created": "Created cells (jsonb).",
    "status": "applied | undone.", "undone_at": "When undone.", "undone_by": "Who undid it."}, **_ENG)

rel("public.proj_po", purpose="Purchase orders (planned and actual): units, vendor, dates, deposit state, unit cost. Finance / supply. " + _FIN, grain="one row per purchase order", source="the Projections app (manual and auto-plan)",
    refresh="on edit", coverage="74 rows", caveats=["Money (unit_cost) and vendor terms: finance. Exposed as analytics.inventory_purchase_orders (the link column is left out)."],
    cols={"id": "Primary key.", "sheet_row": "Row on the sheet's Purchase Orders tab when seeded.", "po_number": "PO number.", "product": "bull | liver | boneflex | belle | flow.", "vendor": "proj_vendor_lead.vendor.",
          "warehouse": "revamp | intl.", "units": "Units ordered.", "order_date": "Order date (NULL = land date minus lead time).", "land_date": "Landing date.", "status": "planned | ordered | in transit | landed | cancelled.",
          "deposit_paid": "Deposit paid.", "shipping_paid": "Shipping paid.", "balance_paid": "Balance paid.", "deposit_pct": "Deposit share (NULL = vendor default).",
          "unit_cost": "Unit cost (NULL = vendor price list).", "link": "Link.", "note": "Note.", "source": "manual | auto-plan.", "created_at": "Created.", "updated_at": "Updated."}, **_ENG)
rel("public.proj_fba_transfer", purpose="Planned and actual transfers from the US warehouse to FBA. " + _FIN, grain="one row per transfer", source="the Projections app", refresh="on edit", coverage="44 rows",
    cols={"id": "Primary key.", "sheet_row": "Row on the sheet when seeded.", "transfer_no": "Transfer number.", "product": "Product key.", "units": "Units.", "ship_date": "Leaves the warehouse stock this month.",
          "receive_date": "Arrives in FBA stock this month.", "status": "planned | shipped | received | cancelled.", "note": "Note.", "source": "manual | auto-plan.", "created_at": "Created.", "updated_at": "Updated."}, **_ENG)
rel("public.proj_vendor_lead", purpose="Vendor lead times, minimum order quantities and default warehouses. " + _FIN, grain="one row per vendor", source="typed", refresh="on edit", coverage="2 vendors",
    cols={"vendor": "Vendor key.", "lead_weeks": "Order date = land date minus this.", "moq_units": "Minimum order quantity.", "default_warehouse": "revamp | intl.", "note": "Note.", "updated_at": "When changed."}, **_ENG)
rel("public.proj_vendor_cogs", purpose="Vendor unit costs per product and default deposit share. Finance. " + _FIN, grain="one row per (vendor, product)", source="typed", refresh="on edit", coverage="6 rows",
    cols={"vendor": "Vendor key.", "product": "Product key.", "unit_cost": "Unit cost.", "deposit_pct": "Default deposit share for new POs.", "note": "Note.", "updated_at": "When changed."}, **_ENG)
rel("public.proj_product_cost", purpose="Unit cost used to value stock on hand (China landed cost per SKU). Finance. " + _FIN, grain="one row per product", source="typed", refresh="on edit", coverage="5 rows",
    cols={"product": "Product key.", "sku": "SKU.", "china_unit_cost": "China landed cost per unit.", "us_unit_cost": "US cost per unit.", "note": "Note.", "updated_at": "When changed."}, **_ENG)
rel("public.proj_reorder_param", purpose="Reorder parameters per location and product (lead days, safety days, MOQ). " + _FIN, grain="one row per (location, product)", source="typed", refresh="on edit", coverage="8 rows",
    cols={"location": "revamp | intl.", "product": "Product key.", "lead_days": "PO placed to landed.", "safety_days": "Safety stock in days.", "moq_units": "Minimum order quantity.", "updated_at": "When changed."}, **_ENG)
rel("public.proj_stock_snapshot", purpose="Stock on hand per location and product, one row per count. " + _FIN, grain="one row per (location, product, as_of)", source="counts and 3PL portals",
    refresh="manual", coverage="20 rows", cols={"location": "revamp | amazon | intl.", "product": "Product key.", "as_of": "Count date.", "on_hand": "Units on hand.", "allocated": "Allocated / pending fulfillment.",
    "inbound_per_3pl": "Units inbound per the 3PL.", "estimate": "true = estimated count.", "note": "Note.", "created_at": "Created."}, **_ENG)
rel("public.proj_supply_history", purpose="One row per changed supply record (orders, transfers, stock, prices) with before and after, for revert / restore. " + _FIN, grain="one row per changed record",
    source="the Projections app", refresh="on edit", coverage="4 rows",
    cols={"id": "Primary key.", "at": "When.", "scenario_slug": "Scenario.", "scenario_version": "Version the change produced.", "actor": "Who.", "action": "What.", "kind": "po | transfer | stock | vendor | vendor_cost | reorder_param | product_cost.",
          "record_id": "Row id or key columns joined with '|'.", "op": "create | update | delete | put.", "old": "Before.", "new": "After.", "reverts_version": "Version this change undoes.", "reverted_by_version": "Version that undid it."}, **_ENG)
rel("public.proj_units_monthly", purpose="Monthly units history for months older than the daily window (Amazon since Sep 2024). " + _FIN, grain="one row per (location, product, month)", source="ExpandFi / typed",
    refresh="rarely", coverage="96 rows", cols={"location": "revamp | amazon | intl.", "product": "Product key.", "month": "First of the month.", "units": "Units.", "source": "Source.", "loaded_at": "When loaded."}, **_ENG)
rel("public.proj_units_country", purpose="International units by destination country and month (informational split of overseas 3PL volume). " + _FIN, grain="one row per (month, country)",
    source="the 3PL's tracking exports", refresh="monthly", coverage="108 rows", cols={"month": "First of the month.", "country": "Country.", "units": "Units.", "days": "Days covered when partial.",
    "source": "Source.", "loaded_at": "When loaded."}, **_ENG)
rel("public.proj_units_loads", purpose="One row per units load (seed, refresh, upload): drives the freshness chips. " + _FIN, grain="one row per load", source="the Projections app / feeds",
    refresh="per load", coverage="18 rows", cols={"id": "Primary key.", "at": "When.", "location": "Location.", "source": "Source.", "rows": "Rows loaded.", "first_day": "First day.", "last_day": "Last day.",
    "actor": "Who.", "note": "Note."}, **_ENG)

# The proj_* tables above carry finance, supply-chain costs, scenarios and the audit trail. None of them is in analytics.


# =====================================================================================================================================
# analytics: the views added by migration 230 (every subject area of the database), one `domain` each, plus the catalog and the metric glossary
# =====================================================================================================================================

DOMAINS = [
    ("marketing", "Meta and Google ads: spend, reach, bids, product splits, platform history"),
    ("attribution", "Which orders each source or campaign gets credit for (Upstack)"),
    ("orders", "Actual revenue, new customers, Amazon sales, site traffic"),
    ("creative", "Creative tracker, family roll-ups, test to scale, ad copy"),
    ("tags", "Products, taxonomy lines, tags, hooks"),
    ("priorities", "The weekly creative priorities list"),
    ("production", "Concepts assigned to vendors and their stages"),
    ("projection", "Projection module: scenarios, levers, P&L inputs and actuals"),
    ("inventory", "Purchase orders, FBA transfers, stock, units shipped"),
    ("cohorts", "Acquisition cohorts and retention curves"),
    ("pacing", "Daily pacing inputs: live today, Amazon month to date, levers"),
    ("competitors", "Meta Ad Library competitor tracking"),
    ("pipeline", "Data freshness, feed health, ETL runs, the catalog and the glossary"),
    ("email", "Klaviyo campaigns, flows, messages: sends, opens, clicks, orders, revenue and the copy (views created by migration 231 once the Klaviyo tables exist)"),
    ("landing_pages", "Landing page copy versions and the ads that send people to each page (views created by migration 231 once those tables exist)"),
    ("amazon", "Amazon (ExpandFi): ads by campaign and ASIN, organic search queries, orders by ASIN, traffic, market baskets, repeat purchases, settlement fees"),
    ("story", "The daily story: facts, short explanation, change patterns and the pattern scoreboard"),
]

BRAIN_VIEWS: set[str] = set()                   # analytics views created by migration 230 (their comments live in that migration)


def _srccols(key: str) -> dict[str, str]:
    spec = REL[key]
    cols: dict[str, str] = {}
    if spec.get("like"):
        cols.update(_srccols(spec["like"]))
    cols.update(spec.get("cols") or {})
    return cols


def _ren(src: str, mapping: dict[str, str]) -> dict[str, str]:
    sc = _srccols(src)
    return {out: sc[col] for out, col in mapping.items()}


def av(name, *, src, domain, purpose, grain, source, refresh, coverage, caveats=(), ren=None, cols=None, examples=(), span=None, base=None):
    """One analytics view of migration 230. `ren` maps a renamed column to its column in `src` (the text is inherited); `cols` adds or overrides text."""
    c = _ren(src, ren or {})
    c.update(cols or {})
    ex = list(examples)
    for t, s in (("First rows", f"SELECT * FROM analytics.{name} LIMIT 20;"), ("Row count", f"SELECT count(*) AS rows FROM analytics.{name};")):
        if len(ex) < 2:
            ex.append((t, s))
    BRAIN_VIEWS.add(f"analytics.{name}")
    rel(f"analytics.{name}", group="analytics", kind="view", like=src, domain=domain, span=span, base=base or src.removeprefix("public."),
        purpose=purpose, grain=grain, source=source, refresh=refresh, coverage=coverage, caveats=list(caveats), cols=c, examples=ex)


_FEEDS_REFRESH = ("nightly: the etl service (13:00 UTC, etl.projection_daily) replaces the trailing 14 days; the feeds service (21:00 UTC) writes the direct pull; "
                  "see analytics.pipeline_feeds for each feed's mode and last load")
_EXPOSED = "Exposed read-only; the private table behind it is never granted."

# ------------------------------------------------------------------------------------------------------------------------------- attribution
av("attribution_source_daily", src="public.proj_feed_attribution", domain="attribution", span=("proj_feed_attribution", "day"),
   purpose="Last-click attribution by ad source (Meta Ads, Google Ads, ...) per Pacific day: new customers and orders each source is credited with, exclusive per order.",
   grain="one row per (day, ad_source)", source="public.proj_feed_attribution (the Upstack last-click feed)", refresh=_FEEDS_REFRESH, coverage="2025-01-01 through yesterday",
   caveats=["Last-click is exclusive per order, unlike the any-click 30-day credit in meta_ad_daily / google_campaign_daily: the two bases do not agree and must not be mixed in one ratio."],
   ren={"new_customers": "nc_orders", "new_customer_revenue": "nc_rev", "order_revenue": "rev"},
   examples=[("New customers and revenue by source, last 30 days", "SELECT ad_source, sum(new_customers) AS new_customers, round(sum(new_customer_revenue)) AS new_customer_revenue "
              "FROM analytics.attribution_source_daily WHERE day > (SELECT max(day) FROM analytics.attribution_source_daily) - 30 GROUP BY 1 ORDER BY 2 DESC;")])

av("attribution_anyclick_daily", src="public.proj_feed_anyclick", domain="attribution", span=("proj_feed_anyclick", "day"),
   purpose="New customers Upstack credits to Scaling and to Testing Meta campaigns per day under any-click 30-day attribution.",
   grain="one row per (day, stage)", source="public.proj_feed_anyclick (the sheet tab 'Upstack Any-Click - By Campaign')", refresh=_FEEDS_REFRESH, coverage="2025-05-08 through yesterday",
   caveats=["Any-click credits every Meta campaign the customer clicked in the 30 days before the order, so a customer can count under both stages across days; do not add to orders_daily.new_customers."],
   ren={"new_customers": "nc", "new_customer_revenue": "nc_rev"},
   examples=[("Testing vs scaling new customers by week", "SELECT date_trunc('week', day)::date AS week, stage, sum(new_customers) AS new_customers FROM analytics.attribution_anyclick_daily GROUP BY 1, 2 ORDER BY 1 DESC, 2;")])

av("attribution_unmapped_daily", src="public.upstack_unmapped_daily", domain="attribution", span=("upstack_unmapped_daily", "day"),
   purpose="Upstack-credited orders the build could not tie to a Meta ad. They are inside orders_daily and inside no ad-level row of meta_ad_daily.",
   grain="one row per (day, campaign, ad)", source="public.upstack_unmapped_daily (rebuilt in full on every build)", refresh="nightly (etl service 13:00 UTC)", coverage="2026-05-06 through yesterday",
   caveats=["reason: 'not_identified' (no ad name / '(not set)'), 'no_campaign' (the campaign matches no Meta campaign), 'no_ad' (campaign found, ad name not)."],
   ren={"new_customers": "nc", "new_customer_revenue": "nc_rev", "order_revenue": "rev"},
   examples=[("Share of credited new customers that match no ad, by reason", "SELECT reason, sum(new_customers) AS new_customers FROM analytics.attribution_unmapped_daily GROUP BY 1 ORDER BY 2 DESC;")])

# ------------------------------------------------------------------------------------------------------------------------------- orders
av("orders_daily_detail", src="public.proj_feed_orders", domain="orders", span=("proj_feed_orders", "day"),
   purpose="orders_daily plus the tax collected and the refunds of each Pacific day: the two columns pacing uses to go from gross to net revenue.",
   grain="one row per day", source="public.proj_feed_orders (Shopify order totals, Upstack)", refresh=_FEEDS_REFRESH, coverage="2025-01-01 through yesterday",
   caveats=["total_revenue is GROSS: tax included, before refunds. Net = total_revenue - tax - refunds. The pacing page and the P&L stay gross and show refunds as a memo only.",
            "The columns shared with orders_daily carry the same values."],
   ren={"new_customer_revenue": "nc_rev", "returning_customer_revenue": "rc_rev", "new_customers": "nc_orders", "total_revenue": "total_rev", "orders": "total_orders"},
   examples=[("Gross vs net revenue by month", "SELECT date_trunc('month', day)::date AS month, round(sum(total_revenue)) AS gross, round(sum(total_revenue - tax - refunds)) AS net, "
              "round(sum(refunds) / NULLIF(sum(total_revenue), 0), 4) AS refund_share FROM analytics.orders_daily_detail GROUP BY 1 ORDER BY 1 DESC;")])

av("orders_by_product_daily", src="public.proj_feed_nc_product", domain="orders", span=("proj_feed_nc_product", "day"),
   purpose="ACTUAL Shopify first-order revenue and new customers per Pacific day by the order's largest product line (not Meta-attributed): the only product split of actual new-customer revenue.",
   grain="one row per (day, product)", source="public.proj_feed_nc_product (ExpandFi merchant 1452 shopify_orders, or the sheet tab 'NC Rev - By Product')", refresh=_FEEDS_REFRESH,
   coverage="2025-01-01 through today; product = VitaBull | Beef Liver | Boneflex | Other",
   caveats=["Revenue = order total_price split across product lines by net line price, tax included, before refunds; new_customers = orders whose largest product line is the product.",
            "Day sums equal orders_daily to within restatement."],
   ren={"new_customer_revenue": "nc_rev", "new_customers": "nc_orders"},
   examples=[("Actual NC revenue and new customers by product, last 30 days", "SELECT product, round(sum(new_customer_revenue)) AS new_customer_revenue, sum(new_customers) AS new_customers "
              "FROM analytics.orders_by_product_daily WHERE day > (SELECT max(day) FROM analytics.orders_by_product_daily) - 30 GROUP BY 1 ORDER BY 2 DESC;")])

av("orders_hourly", src="public.proj_feed_nc_hourly", domain="orders", span=("proj_feed_nc_hourly", "day"),
   purpose="Actual new-customer revenue and first orders by Pacific day and clock hour: the share curve behind the 'NC revenue today' card.",
   grain="one row per (day, hour) with at least one first order", source="public.proj_feed_nc_hourly (ExpandFi warehouse shopify_orders)", refresh="feeds service 21:00 UTC, trailing 35 closed days",
   coverage="from the first pull (a 60-day backfill once); today is never stored",
   caveats=["Sparse: an hour with no first order has no row. Revenue is tax included, before refunds."],
   ren={"new_customer_revenue": "nc_rev", "new_customers": "nc_orders"},
   examples=[("Share of a day's new-customer revenue by hour (last 28 closed days)", "SELECT hour, round(sum(new_customer_revenue) / (SELECT sum(new_customer_revenue) FROM analytics.orders_hourly), 4) AS share "
              "FROM analytics.orders_hourly GROUP BY 1 ORDER BY 1;")])

av("amazon_daily", src="public.proj_feed_amazon", domain="orders", span=("proj_feed_amazon", "day"),
   purpose="Amazon.com + B2B sales and Amazon Ads (de-duplicated across v1 / v2 / v3 report generations) by purchase day.",
   grain="one row per day", source="public.proj_feed_amazon (ExpandFi merchant 1452 sales table, or the sheet tab 'Amazon - Daily')", refresh=_FEEDS_REFRESH, coverage="2024-09-24 through yesterday (about a 2-day lag)",
   caveats=["Amazon is a separate channel: its revenue is not in orders_daily and its ad spend is not in platform_daily. Pacing's business-wide totals add the two."],
   ren={"new_customer_orders": "nc_orders", "new_customer_revenue": "nc_rev"},
   examples=[("Amazon revenue and ad spend by month", "SELECT date_trunc('month', day)::date AS month, round(sum(revenue)) AS revenue, round(sum(ad_spend)) AS ad_spend FROM analytics.amazon_daily GROUP BY 1 ORDER BY 1 DESC;")])

av("site_traffic_daily", src="public.proj_feed_traffic", domain="orders", span=("proj_feed_traffic", "day"),
   purpose="Pixel-tracked visitors, new visitors and sessions per day (Upstack).", grain="one row per day", source="public.proj_feed_traffic", refresh=_FEEDS_REFRESH, coverage="2025-05-08 through today",
   caveats=["Visitors are unique within a day: summing days does not give unique visitors."],
   examples=[("Cost per new visitor by week (Meta + Google spend)", "SELECT date_trunc('week', t.day)::date AS week, round(sum(p.spend) / NULLIF(sum(t.new_visitors), 0), 2) AS cost_per_new_visitor "
              "FROM analytics.site_traffic_daily t JOIN (SELECT day, sum(spend) AS spend FROM analytics.platform_daily GROUP BY 1) p USING (day) GROUP BY 1 ORDER BY 1 DESC LIMIT 12;")])

# ------------------------------------------------------------------------------------------------------------------------------- marketing
av("platform_daily_history", src="public.proj_feed_platform", domain="marketing", span=("proj_feed_platform", "day"),
   purpose="Meta and Google spend and platform-reported conversions per day from 2025-01-01 (Upstack ad-platform feed): the longest spend history in the database.",
   grain="one row per (day, platform)", source="public.proj_feed_platform", refresh=_FEEDS_REFRESH, coverage="2025-01-01 through yesterday",
   caveats=["platform is normalised to 'meta' | 'google' (platform_label keeps Upstack's text). Spend reconciles with meta_ad_daily and google_campaign_daily on the days they share.",
            "own_purchases / own_revenue are platform-reported and overlap across platforms: never add them. TikTok has no rows."],
   ren={"platform_label": "platform", "own_purchases": "purchases", "own_revenue": "purchase_value", "own_new_customer_purchases": "nc_purchases", "own_new_customer_revenue": "nc_purchase_value"},
   cols={"platform": "'meta' | 'google' (normalised from platform_label)."},
   examples=[("Spend by platform and month since 2025", "SELECT date_trunc('month', day)::date AS month, platform, round(sum(spend)) AS spend FROM analytics.platform_daily_history GROUP BY 1, 2 ORDER BY 1 DESC, 2;")])

av("meta_product_daily", src="public.proj_feed_meta_product", domain="marketing", span=("proj_feed_meta_product", "day"),
   purpose="Meta spend and link clicks per day by product, classified campaign name -> ad set name -> ad name -> Other.", grain="one row per (day, product) after the day rule",
   source="public.proj_feed_meta_product", refresh="nightly (app rows, trailing 14 days)", coverage="2025-01-01 through yesterday",
   caveats=["A day is read from 'app' rows when it has any, else from the sheet rows (the view applies this, so no day is counted twice).",
            "Campaigns that mix products are split by their ad set and ad names where the campaign name is not enough, so this can differ from meta_ads.product_slug."],
   examples=[("Meta spend by product, last 30 days", "SELECT product, round(sum(spend)) AS spend, sum(link_clicks) AS link_clicks FROM analytics.meta_product_daily "
              "WHERE day > (SELECT max(day) FROM analytics.meta_product_daily) - 30 GROUP BY 1 ORDER BY 2 DESC;")])

av("meta_product_reach", src="public.proj_feed_product_reach", domain="marketing", span=("proj_feed_product_reach", "period_start", "period_end"),
   purpose="Unique outbound clicks per product, de-duplicated across the product's ad sets, at day / week / month grain: the only per-product reach-like metric.",
   grain="one row per (grain, period_key, product)", source="public.proj_feed_product_reach", refresh=_FEEDS_REFRESH, coverage="from 2026-07-05 (Meta serves filtered unique clicks about 120 days back); earlier periods are absent and never re-pulled",
   caveats=["Not additive across periods or products: read the row of the grain you want. The three products add to about 1.01-1.06x the account-level unique outbound clicks in meta_reach.",
            "Reach and frequency per product do not exist, only unique outbound clicks."],
   examples=[("Unique outbound clicks per product, last 4 weeks", "SELECT period_key, product, unique_outbound_clicks FROM analytics.meta_product_reach WHERE grain = 'week' ORDER BY period_start DESC, product LIMIT 16;")])

av("meta_bids_daily", src="public.proj_feed_bids", domain="marketing", span=("proj_feed_bids", "day"),
   purpose="Meta bid settings per day, summed: spend on cost-cap / bid-cap ad sets and on ROAS-floor ad sets, with the spend-weighted bid sums.", grain="one row per day",
   source="public.proj_feed_bids (the sheet tab 'Meta Bids - Daily')", refresh=_FEEDS_REFRESH, coverage="2026-04-28 through today; settings of past days are rebuilt from the account activity log, so older history cannot be pulled",
   caveats=["Average cost cap = sum(spend_times_cap) / sum(capped_spend); average ROAS floor = sum(spend_times_floor) / sum(floor_spend). Per-ad-set bids are not stored."],
   ren={"spend_times_cap": "spend_x_cap", "spend_times_floor": "spend_x_floor"},
   examples=[("Share of spend on capped ad sets and average cost cap, by week", "SELECT date_trunc('week', day)::date AS week, round(sum(capped_spend) / NULLIF(sum(spend), 0), 3) AS capped_share, "
              "round(sum(spend_times_cap) / NULLIF(sum(capped_spend), 0), 2) AS avg_cost_cap FROM analytics.meta_bids_daily GROUP BY 1 ORDER BY 1 DESC;")])

# ------------------------------------------------------------------------------------------------------------------------------- pacing
av("pacing_live_today", src="public.proj_live_today_snapshots", domain="pacing", span=("proj_live_today_snapshots", "day"),
   purpose="Today's Meta spend, Google spend, new-customer revenue and new customers so far, one snapshot per hourly pull from Upstack.", grain="one row per pull (pulled_at, to the minute)",
   source="public.proj_live_today_snapshots (etl.live_today)", refresh="Railway cron `live`, every hour on the hour", coverage="from the first pull; history kept 90 days",
   caveats=["Upstack trails the ad platforms by 30-60 minutes, so the numbers are a floor of what was spent and sold; end-of-day projections made early in the day lean low.",
            "Revenue is tax included, before refunds (gross)."],
   ren={"new_customer_revenue": "nc_rev", "new_customers": "nc_orders"},
   examples=[("The latest snapshot of each day", "SELECT DISTINCT ON (day) day, pacific_time, meta_spend, total_spend, new_customer_revenue, new_customers FROM analytics.pacing_live_today ORDER BY day DESC, pulled_at DESC LIMIT 14;")])

av("pacing_amazon_month_to_date", src="public.proj_amazon_mtd_snapshots", domain="pacing", span=("proj_amazon_mtd_snapshots", "snapshot_day"),
   purpose="Completed Amazon month-to-date revenue, one snapshot per Pacific day and month: reported days plus the missing days projected at the trailing 7-day average of the reported days.",
   grain="one row per (snapshot_day, month)", source="public.proj_amazon_mtd_snapshots (etl.amazon_mtd), without the projected_days json", refresh="after the Amazon feed load (feeds 21:00 UTC) and again in the etl service (13:00 UTC)",
   coverage="from the first run; history kept so each day's projection can be compared with the final month",
   caveats=["projected_mtd is an estimate, never an actual; the projected days themselves (json) are left out, rebuild them from daily_rate and days_projected."],
   examples=[("Latest Amazon month-to-date projection per month", "SELECT DISTINCT ON (month) month, snapshot_day, actual_mtd, projected_mtd, status FROM analytics.pacing_amazon_month_to_date ORDER BY month DESC, snapshot_day DESC LIMIT 6;")])

av("pacing_hourly_spend_curve", src="public.proj_pacing_hourly", domain="pacing", base="proj_pacing_hourly",
   purpose="Meta time-of-day spend curve (the last 30 days' hourly spend): the typed input behind the intraday projection.", grain="one row per hour of day (Pacific)",
   source="public.proj_pacing_hourly (typed from Meta Ads Manager's time-of-day report)", refresh="manual", coverage="24 rows",
   examples=[("Share of daily spend by hour", "SELECT hour, round(spend_30d / sum(spend_30d) OVER (), 4) AS share FROM analytics.pacing_hourly_spend_curve ORDER BY hour;")])

av("pacing_levers", src="public.proj_feed_levers", domain="pacing", base="proj_feed_levers",
   purpose="The ratios and estimates the pacing page applies (for example the overseas-warehouse units ratio), one row per lever.", grain="one row per lever",
   source="public.proj_feed_levers (the updated_by column is left out)", refresh="on change", coverage="a handful of rows",
   ren={"lever": "key"},
   examples=[("All levers", "SELECT lever, value, note FROM analytics.pacing_levers ORDER BY lever;")])

av("pacing_meta_spend_monthly", src="public.proj_meta_spend_monthly", domain="pacing", span=("ad_daily", "day"), base="ad_daily",
   purpose="Monthly Meta spend from ad_daily, read by the projection drivers module.", grain="one row per month", source="public.proj_meta_spend_monthly (a view over ad_daily)", refresh="live view",
   coverage="2026-05 onward", caveats=["A month is complete when days equals the days in the month."],
   examples=[("Monthly Meta spend", "SELECT month, spend, days FROM analytics.pacing_meta_spend_monthly ORDER BY month DESC;")])

# ------------------------------------------------------------------------------------------------------------------------------- pipeline
av("pipeline_feeds", src="public.proj_feed_runs", domain="pipeline", base="proj_feed_runs",
   purpose="One row per pacing feed: its mode (sheet / shadow / direct), what was last loaded and through which day.", grain="one row per feed",
   source="public.proj_feed_runs joined to public.proj_feed_config", refresh="each load", coverage="about 12 feeds",
   cols={"mode": "'sheet' | 'shadow' | 'direct' (no config row = 'sheet'): which source the app reads for the feed."},
   examples=[("Feeds, stalest first", "SELECT feed, mode, loaded_at, last_day, rows_loaded FROM analytics.pipeline_feeds ORDER BY last_day NULLS FIRST;")])

av("pipeline_feed_attempts", src="public.proj_feed_attempts", domain="pipeline", span=("proj_feed_attempts", "started_at::date"),
   purpose="One row per direct pull attempt of a feed (any mode).", grain="one row per attempt", source="public.proj_feed_attempts", refresh="each feeds run (21:00 UTC)", coverage="since 2026-11-04",
   examples=[("Failed attempts, newest first", "SELECT feed, mode, started_at, status, note FROM analytics.pipeline_feed_attempts WHERE status <> 'ok' ORDER BY started_at DESC LIMIT 20;")])

av("pipeline_feed_verify", src="public.proj_feed_verify", domain="pipeline", span=("proj_feed_verify", "day"),
   purpose="Latest verification result per feed and day: the direct pull compared with the sheet-fed table.", grain="one row per (feed, day)", source="public.proj_feed_verify (etl.feeds verify)",
   refresh="each feeds run", coverage="a trailing window per feed",
   examples=[("Days that did not verify", "SELECT feed, day, status, worst_col, worst_rel FROM analytics.pipeline_feed_verify WHERE status <> 'ok' ORDER BY day DESC LIMIT 20;")])

av("pipeline_refresh_runs", src="public.proj_refresh_runs", domain="pipeline", span=("proj_refresh_runs", "started_at::date"),
   purpose="One row per projection module per run of the nightly projection refresh.", grain="one row per module run", source="public.proj_refresh_runs (etl.projection_daily)", refresh="nightly",
   coverage="since the projection module went live", cols={"error": "Scrubbed error text, first 300 characters."},
   examples=[("Latest run of each module", "SELECT DISTINCT ON (module) module, started_at, status, rows, data_through FROM analytics.pipeline_refresh_runs ORDER BY module, started_at DESC;")])

av("pipeline_alerts", src="public.alert_state", domain="pipeline", span=("alert_state", "first_seen::date", "last_seen::date"),
   purpose="The hourly health checks that fired: when each problem opened, was last seen, was last alerted and whether it resolved.", grain="one row per check id",
   source="public.alert_state (etl.alerts), without the fingerprint", refresh="hourly", coverage="since the alerts service went live",
   cols={"last_message": "The last alert text, scrubbed of tokens, first 300 characters."},
   examples=[("Open problems", "SELECT check_id, first_seen, last_seen, alert_count, last_message FROM analytics.pipeline_alerts WHERE resolved_at IS NULL ORDER BY first_seen;")])

# ------------------------------------------------------------------------------------------------------------------------------- projection
av("projection_scenarios", src="public.proj_scenarios", domain="projection", base="proj_scenarios",
   purpose="The named scenarios (lever sets) of the Projection module; the one with is_default is the plan of record.", grain="one row per scenario",
   source="public.proj_scenarios without the params blob (read the levers through projection_levers)", refresh="on edit", coverage="about 7 scenarios",
   caveats=["archived_at set = deleted (kept 30 days): filter archived_at IS NULL for live scenarios. The model's OUTPUT (P&L, cash, financing results) is computed by the app on demand and is not stored; "
            "read it from the HTTP API /api/projection/..., and read the inputs here."],
   ren={"scenario_id": "id"}, cols={"archived_at": "Set when the scenario was deleted (kept 30 days); NULL = live."},
   examples=[("Live scenarios", "SELECT slug, name, is_default, version, updated_at FROM analytics.projection_scenarios WHERE archived_at IS NULL ORDER BY is_default DESC, slug;")])

av("projection_levers", src="public.proj_scenarios", domain="projection", base="proj_scenarios",
   purpose="Every lever of every live scenario, one row per (scenario, module, lever): financing terms, driver rates (sales tax, channel shares), cohort levers (curve source, cohorts averaged), pacing and inventory settings.",
   grain="one row per (scenario_slug, module, lever)", source="public.proj_scenarios.params, flattened two levels deep", refresh="on edit", coverage="every live scenario",
   caveats=["value is json: a number, text, a list or a nested object depending on the lever. Cast with value #>> '{}' for scalars.",
            "A scenario holds only the levers it changes; the app falls back to base_slug and then to code defaults, which are not rows here."],
   cols={"scenario_slug": "proj_scenarios.slug.", "module": "Projection module that owns the lever: financing | drivers | cohorts | pacing | inventory_demand | inventory_supply | pnl ...",
         "lever": "Lever name inside the module (for example sales_tax_rate in drivers, curve_source in cohorts).", "value": "The lever's value as json."},
   examples=[("The plan of record's drivers and cohort levers", "SELECT module, lever, value FROM analytics.projection_levers WHERE scenario_slug = (SELECT slug FROM analytics.projection_scenarios WHERE is_default AND archived_at IS NULL) "
              "AND module IN ('drivers', 'cohorts') ORDER BY module, lever;")])

av("projection_series", src="public.proj_series", domain="projection", span=("proj_series", "month"),
   purpose="The monthly cells a scenario itself holds: inputs ('input') and overrides ('override') of a line for a month.", grain="one row per (scenario_slug, line_key, month)",
   source="public.proj_series joined to public.proj_scenarios", refresh="on edit", coverage="about 1.6K cells",
   caveats=["Only cells the scenario itself holds; cells it inherits from its base scenario are not repeated here."],
   cols={"scenario_slug": "proj_scenarios.slug."},
   examples=[("Cells of the plan of record", "SELECT line_key, month, value, kind FROM analytics.projection_series WHERE scenario_slug = (SELECT slug FROM analytics.projection_scenarios WHERE is_default AND archived_at IS NULL) ORDER BY line_key, month LIMIT 50;")])

av("projection_actuals", src="public.proj_actuals", domain="projection", span=("proj_actuals", "month"),
   purpose="Scenario-independent monthly facts: QuickBooks P&L lines (line_key 'pnl.<line>'), spend by channel ('spend.<channel>') and other loaded actuals.", grain="one row per (line_key, month)",
   source="public.proj_actuals (QuickBooks uploads and nightly roll-ups)", refresh="monthly upload / nightly", coverage="about 825 cells",
   examples=[("One P&L line by month", "SELECT month, value FROM analytics.projection_actuals WHERE line_key LIKE 'pnl.%' ORDER BY line_key, month DESC LIMIT 36;")])

av("projection_pnl_drivers", src="public.proj_pnl_drivers", domain="projection", base="proj_pnl_drivers",
   purpose="How each P&L line is projected: an ordered list of fixed / carry / rate segments (a model input).", grain="one row per P&L line", source="public.proj_pnl_drivers",
   refresh="on edit", coverage="about 32 lines", caveats=["segments is json describing the projection rule of the line; it holds no actual values."],
   examples=[("All driver lines", "SELECT line_key, jsonb_array_length(segments) AS segments, source FROM analytics.projection_pnl_drivers ORDER BY line_key;")])

av("projection_pnl_settings", src="public.proj_pnl_settings", domain="projection", base="proj_pnl_settings",
   purpose="P&L engine settings (rate window, inflation, headcount, COGS, enterprise value), one row per setting.", grain="one row per setting", source="public.proj_pnl_settings",
   refresh="on edit", coverage="about 6 settings", ren={"setting": "key"},
   examples=[("All settings", "SELECT setting, value, note FROM analytics.projection_pnl_settings ORDER BY setting;")])

av("projection_pnl_account_map", src="public.proj_pnl_qb_map", domain="projection", base="proj_pnl_qb_map",
   purpose="QuickBooks account name to P&L line key map used when an export is loaded.", grain="one row per account", source="public.proj_pnl_qb_map", refresh="on edit", coverage="about 61 accounts",
   examples=[("Accounts per P&L line", "SELECT line_key, count(*) AS accounts FROM analytics.projection_pnl_account_map GROUP BY 1 ORDER BY 2 DESC;")])

av("projection_cogs_sku", src="public.proj_pnl_cogs_sku", domain="projection", base="proj_pnl_cogs_sku",
   purpose="Landed unit cost by SKU and the sales mix that weights it (the forward COGS model).", grain="one row per SKU", source="public.proj_pnl_cogs_sku", refresh="on edit", coverage="about 12 SKUs",
   examples=[("Mix-weighted landed cost per unit (China vendor)", "SELECT round(sum(china_unit_cost * sales_mix) / NULLIF(sum(sales_mix), 0), 4) AS weighted_cost FROM analytics.projection_cogs_sku;")])

av("projection_vendor_retainers", src="public.proj_pnl_vendors", domain="projection", base="proj_pnl_vendors",
   purpose="Vendor retainers, current vs proposed, per month (reference).", grain="one row per vendor", source="public.proj_pnl_vendors", refresh="on edit", coverage="about 10 vendors",
   examples=[("Retainers", "SELECT vendor, current_monthly, proposed_monthly FROM analytics.projection_vendor_retainers ORDER BY sort;")])

# ------------------------------------------------------------------------------------------------------------------------------- inventory
av("inventory_purchase_orders", src="public.proj_po", domain="inventory", span=("proj_po", "land_date"),
   purpose="Purchase orders (planned and actual): units, vendor, warehouse, dates, deposit state and unit cost.", grain="one row per purchase order",
   source="public.proj_po without the link column", refresh="on edit", coverage="about 74 rows",
   caveats=["status 'landed' means the units are already in the on-hand stock snapshot: do not count them as inbound again. order_date NULL = land_date minus the vendor lead time."],
   ren={"po_id": "id"},
   examples=[("Units still to land by warehouse and product", "SELECT warehouse, product, sum(units) AS units FROM analytics.inventory_purchase_orders WHERE status NOT IN ('landed', 'cancelled') GROUP BY 1, 2 ORDER BY 1, 2;")])

av("inventory_fba_transfers", src="public.proj_fba_transfer", domain="inventory", span=("proj_fba_transfer", "receive_date"),
   purpose="Planned and actual transfers from the US warehouse to FBA.", grain="one row per transfer", source="public.proj_fba_transfer", refresh="on edit", coverage="about 44 rows",
   ren={"transfer_id": "id"},
   examples=[("Units in transit to FBA by receive month", "SELECT date_trunc('month', receive_date)::date AS receive_month, product, sum(units) AS units FROM analytics.inventory_fba_transfers WHERE status NOT IN ('received', 'cancelled') GROUP BY 1, 2 ORDER BY 1, 2;")])

av("inventory_vendor_lead_times", src="public.proj_vendor_lead", domain="inventory", base="proj_vendor_lead",
   purpose="Vendor lead times, minimum order quantities and default warehouses.", grain="one row per vendor", source="public.proj_vendor_lead", refresh="on edit", coverage="2 vendors",
   examples=[("Lead times", "SELECT vendor, lead_weeks, moq_units, default_warehouse FROM analytics.inventory_vendor_lead_times;")])

av("inventory_vendor_costs", src="public.proj_vendor_cogs", domain="inventory", base="proj_vendor_cogs",
   purpose="Vendor unit cost per product and the default deposit share of a new purchase order.", grain="one row per (vendor, product)", source="public.proj_vendor_cogs", refresh="on edit", coverage="about 6 rows",
   examples=[("Vendor price list", "SELECT vendor, product, unit_cost, deposit_pct FROM analytics.inventory_vendor_costs ORDER BY vendor, product;")])

av("inventory_product_costs", src="public.proj_product_cost", domain="inventory", base="proj_product_cost",
   purpose="Unit cost used to value stock on hand (landed cost per SKU).", grain="one row per product", source="public.proj_product_cost", refresh="on edit", coverage="5 rows",
   examples=[("Costs", "SELECT product, sku, china_unit_cost, us_unit_cost FROM analytics.inventory_product_costs ORDER BY product;")])

av("inventory_reorder_params", src="public.proj_reorder_param", domain="inventory", base="proj_reorder_param",
   purpose="Reorder parameters per location and product: lead days, safety days, minimum order quantity.", grain="one row per (location, product)", source="public.proj_reorder_param", refresh="on edit", coverage="about 8 rows",
   examples=[("Parameters", "SELECT location, product, lead_days, safety_days, moq_units FROM analytics.inventory_reorder_params ORDER BY 1, 2;")])

av("inventory_stock_counts", src="public.proj_stock_snapshot", domain="inventory", span=("proj_stock_snapshot", "as_of"),
   purpose="Stock on hand per location and product, one row per count (counts and 3PL portal readings).", grain="one row per (location, product, as_of)", source="public.proj_stock_snapshot", refresh="manual",
   coverage="about 20 rows", caveats=["estimate = true marks a count that was estimated, not read from a portal."],
   examples=[("Latest count per location and product", "SELECT DISTINCT ON (location, product) location, product, as_of, on_hand, allocated, estimate FROM analytics.inventory_stock_counts ORDER BY location, product, as_of DESC;")])

av("inventory_stock_daily", src="public.proj_demand_snapshot", domain="inventory", span=("proj_demand_snapshot", "as_of"),
   purpose="Stock on hand and allocated per location and product, one snapshot per day (Warehance for the US warehouse).", grain="one row per (location, product, as_of)", source="public.proj_demand_snapshot",
   refresh="nightly", coverage="2026-11-03 onward",
   examples=[("Latest stock", "SELECT location, product, as_of, on_hand, allocated FROM analytics.inventory_stock_daily WHERE as_of = (SELECT max(as_of) FROM analytics.inventory_stock_daily) ORDER BY 1, 2;")])

av("inventory_units_daily", src="public.proj_units_daily", domain="inventory", span=("proj_units_daily", "day"),
   purpose="Units shipped or sold per day by location (revamp = US 3PL, amazon = FBA, intl = overseas 3PL) and product: the demand history.", grain="one row per (location, product, day)",
   source="public.proj_units_daily (Warehance, ExpandFi, the 3PL's tracking exports and an estimate)", refresh="nightly feeds",
   coverage="amazon 2024-09-24.., intl 2026-07-01.., revamp 2026-07-17.. (US shipments exist only from the warehouse go-live)",
   caveats=["is_estimate = true rows are modeled (international: Warehance non-US units x a fitted ratio)."],
   examples=[("Units per week by location", "SELECT date_trunc('week', day)::date AS week, location, sum(units) AS units FROM analytics.inventory_units_daily GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT 24;")])

av("inventory_units_monthly", src="public.proj_units_monthly", domain="inventory", span=("proj_units_monthly", "month"),
   purpose="Monthly units history for months older than the daily window (Amazon since Sep 2024).", grain="one row per (location, product, month)", source="public.proj_units_monthly", refresh="rarely", coverage="about 96 rows",
   examples=[("Units per month by location", "SELECT month, location, sum(units) AS units FROM analytics.inventory_units_monthly GROUP BY 1, 2 ORDER BY 1 DESC, 2;")])

av("inventory_units_by_country", src="public.proj_units_country", domain="inventory", span=("proj_units_country", "month"),
   purpose="International units by destination country and month (informational split of the overseas 3PL volume).", grain="one row per (month, country)", source="public.proj_units_country",
   refresh="monthly", coverage="about 108 rows",
   examples=[("Top countries, latest month", "SELECT country, units FROM analytics.inventory_units_by_country WHERE month = (SELECT max(month) FROM analytics.inventory_units_by_country) ORDER BY units DESC LIMIT 10;")])

# ------------------------------------------------------------------------------------------------------------------------------- cohorts
av("cohorts_monthly", src="public.proj_cohorts", domain="cohorts", span=("proj_cohorts", "cohort_month"),
   purpose="One row per acquisition month: new customers, first-order dollars per customer and in total, CAC, 90-day repeat rate and payback as exported.", grain="one row per cohort_month",
   source="public.proj_cohorts (cohort export uploads)", refresh="monthly upload", coverage="about 33 cohorts",
   caveats=["Only closed months belong in the model (a month that is not over is blanked on upload). Cohort dollars differ by export source (Shopify vs Upstack): do not mix sources between refreshes.",
            "cac and repeat_90d for the newest cohorts can be blank until final."],
   cols={"first_order_revenue": "new_customers x first_order_per_customer: the cohort's first-order revenue (the base the repeat curve multiplies)."},
   examples=[("Cohort size, first-order dollars and CAC", "SELECT cohort_month, new_customers, round(first_order_per_customer, 2) AS first_order, round(cac, 2) AS cac FROM analytics.cohorts_monthly ORDER BY cohort_month DESC LIMIT 12;")])

av("cohorts_curve_cumulative", src="public.proj_cohort_cum", domain="cohorts", span=("proj_cohort_cum", "cohort_month"),
   purpose="Cumulative dollars per cohort customer at each age (months after acquisition), with the monthly increment, the increment as a multiple of the first order and the cohort's recency rank at that age: the exact inputs of the Live retention curve.",
   grain="one row per (cohort_month, age)", source="public.proj_cohort_cum joined to public.proj_cohorts and itself (age - 1)", refresh="monthly upload", coverage="about 33 cohorts x 36 ages",
   caveats=["Mirrors app/projection/cohorts/engine.py: a cell counts only when cum_per_customer is non-null and non-zero; the increment at age 1 is measured from first_order_per_customer.",
            "Live curve at age a = sum(new_customers x increment_ratio) / sum(new_customers) over the rows with usable_rank <= N (N = the cohorts_n lever, 3 in code, 6 on the sheet); "
            "ages from tail_from_age (28) on = previous age x tail_decay (0.95). usable_rank 1 = the newest cohort that has a usable increment at that age."],
   cols={"new_customers": "Customers in the cohort (the weight in the Live curve).", "first_order_per_customer": "The cohort's first-order dollars per customer.",
         "increment": "Dollars per customer earned in this month of age: cum_per_customer minus the previous age (age 1: minus the first order). NULL when this or the previous cell is not observed.",
         "increment_ratio": "increment / first_order_per_customer: repeat dollars per $1 of first-order dollars in this month of age (the curve's unit).",
         "usable_rank": "1 = the newest cohort whose increment_ratio at this age is usable (non-null, non-zero, new_customers > 0), 2 = the next, ...; NULL when this cell is not usable."},
   examples=[("Live retention curve over the last 3 cohorts (ages 1-27)", "SELECT age, round(sum(new_customers * increment_ratio) / NULLIF(sum(new_customers), 0), 4) AS live_curve FROM analytics.cohorts_curve_cumulative "
              "WHERE usable_rank <= 3 AND age < 28 GROUP BY 1 ORDER BY 1;"),
             ("Cumulative dollars per customer by age for the newest 6 cohorts", "SELECT cohort_month, age, round(cum_per_customer, 2) AS cum_per_customer FROM analytics.cohorts_curve_cumulative "
              "WHERE cohort_month >= (SELECT max(cohort_month) FROM analytics.cohorts_monthly) - interval '5 months' ORDER BY cohort_month, age;")])

av("cohorts_curve_goal", src="public.proj_curve_goal", domain="cohorts", span=("proj_curve_goal", "snapshot_date"),
   purpose="Goal retention curve snapshots: repeat dollars per $1 of first-order revenue by age 1..60. The newest snapshot (is_active) is the Goal curve the model uses.", grain="one row per (snapshot_id, age)",
   source="public.proj_curve_goal without saved_by", refresh="on save", coverage="60 rows per saved snapshot",
   cols={"is_active": "true on every row of the newest snapshot (the highest snapshot_id), the one the model reads as the Goal curve."},
   examples=[("The active Goal curve with lifetime and 12-month sums", "SELECT sum(value) AS lifetime_60, sum(value) FILTER (WHERE age <= 12) AS first_12_months FROM analytics.cohorts_curve_goal WHERE is_active;")])

# ------------------------------------------------------------------------------------------------------------------------------- creative roll-ups
_BASIS = ("basis: 'meta' (Meta-reported purchases and revenue, counts returning customers) | 'upstack_new_customers' (Upstack first orders, any-click 30 days) | 'upstack_all_orders' "
          "(every order Upstack credits). Spend and impressions are Meta's in all three; filter basis = 'meta' unless you mean an Upstack basis, and never add rows of different bases.")

av("creative_family_daily", src="public.family_daily", domain="creative", span=("family_daily", "day"), base="family_daily",
   purpose="Spend, purchases and revenue per creative family (tracker family) per day and stage, in three bases and for all ads or only the ads that were active that day.",
   grain="one row per (basis, ads_scope, family_key, day, stage)", source="public.family_daily, family_daily_active and their _upstack_nc / _upstack_all twins (rebuilt in full by etl.build_metrics), plus the product of the family's first tracker row",
   refresh="nightly (etl service 13:00 UTC)", coverage="2026-05-06 through yesterday (Pacific); older days exist only in the sheet-era backfill",
   caveats=[_BASIS, "ads_scope: 'all_ads' = every ad of the family; 'active_ads' = only ads active that day. A family can span several tracker rows: key on family_key.",
            "Filter basis and ads_scope in the WHERE clause: the planner then reads one table instead of six."],
   cols={"basis": _BASIS, "ads_scope": "'all_ads' | 'active_ads'.", "product_slug": "Product of the family's first tracker row (NULL when the family has none)."},
   examples=[("Spend and ROAS per product, last 30 days (Meta basis)", "SELECT product_slug, round(sum(spend)) AS spend, round(sum(revenue) / NULLIF(sum(spend), 0), 2) AS roas FROM analytics.creative_family_daily "
              "WHERE basis = 'meta' AND ads_scope = 'all_ads' AND day > (SELECT max(day) FROM analytics.creative_family_daily WHERE basis = 'meta' AND ads_scope = 'all_ads') - 30 GROUP BY 1 ORDER BY 2 DESC;")])

av("creative_family_metrics", src="public.ad_metrics", domain="creative", base="ad_metrics",
   purpose="Since-launch, 7-day and 30-day spend, purchases and revenue per tracker row, with impressions, clicks and the number of active ads, in three bases.", grain="one row per (basis, tracker_row_id)",
   source="public.ad_metrics and its _upstack_nc / _upstack_all twins (etl.build_metrics), with the tracker row's concept id and the product", refresh="nightly (etl service 13:00 UTC)",
   coverage="every live tracker row; spend from 2026-05-06",
   caveats=[_BASIS, "Metrics sit on the FIRST row of each ad family (is_first_row): sum rows, count unique ads with count(*) FILTER (WHERE is_first_row).",
            "'Launched' (the denominator of the display hit rate) = first rows with a launch_date or any spend."],
   cols={"basis": _BASIS, "concept_id": "Creative Tracker concept id of the row.", "product_slug": "Product of the tracker row."},
   examples=[("Hit rate by product: launched ads that reached $10,000 since launch (Meta basis)", "SELECT product_slug, count(*) AS launched, count(*) FILTER (WHERE spend_launch >= 10000) AS hits, "
              "round(count(*) FILTER (WHERE spend_launch >= 10000)::numeric / count(*), 4) AS hit_rate FROM analytics.creative_family_metrics "
              "WHERE basis = 'meta' AND is_first_row AND (launch_date IS NOT NULL OR spend_launch > 0) GROUP BY 1 ORDER BY 1;")])

av("creative_unmatched_meta", src="public.unmatched_meta", domain="creative", base="unmatched_meta",
   purpose="Meta ad families that run on no Creative Tracker row, with their spend and results, in three bases.", grain="one row per (basis, family_key)",
   source="public.unmatched_meta and its twins (etl.build_metrics), with the product slug", refresh="nightly (etl service 13:00 UTC)", coverage="families with Meta delivery since 2026-05-06",
   caveats=[_BASIS], ren={"family_key": "meta_family_key", "family_name": "meta_family"},
   cols={"basis": _BASIS, "product_slug": "Product guessed from the family's name, campaign or ad set (product_source says which)."},
   examples=[("Untracked spend, last 30 days (Meta basis)", "SELECT product_slug, count(*) AS families, round(sum(spend_30d)) AS spend_30d FROM analytics.creative_unmatched_meta WHERE basis = 'meta' GROUP BY 1 ORDER BY 3 DESC;")])

av("creative_stage_moves", src="public.creative_stage_moves", domain="creative", span=("creative_stage_moves", "first_day", "last_spend_day"), base="creative_stage_moves",
   purpose="Test to scale: one row per creative family with its testing and scaling spend and results, when it first spent in a Testing and in a Scaling campaign and whether it moved, in three bases.",
   grain="one row per (basis, family_key)", source="public.creative_stage_moves and its twins (etl.stage_moves, rebuilt nightly)", refresh="nightly (etl service 13:00 UTC)", coverage="2026-05-06 through yesterday (Pacific)",
   caveats=[_BASIS, "A creative MOVED when it spent in a Testing campaign and its first spend in a Scaling campaign came on a later day (campaign name contains 'test' = Testing). Rate of reaching scaling = moved / began_in_testing over creatives "
            "launched in the window and not launched_before_data_start (app/promotions.py).",
            "moves_that_day >= 40 (bulk_move_day) is a mass relaunch into new scaling campaigns, not a set of test winners: exclude it when judging test winners.",
            "Held up? = (scale_revenue / scale_spend) / (test_revenue / test_spend): held >= 0.90, softer >= 0.70, fell below; too early until scale_spend >= $200 and scale_days >= 3."],
   ren={"pain_point": "pain"}, cols={"basis": _BASIS},
   examples=[("Test-to-scale rate by product, creatives launched in the last 30 days (Meta basis, bulk days hidden)", "SELECT product_slug, count(*) AS testers, count(*) FILTER (WHERE moved) AS moved, "
              "round(count(*) FILTER (WHERE moved)::numeric / count(*), 4) AS pct_reached_scaling FROM analytics.creative_stage_moves WHERE basis = 'meta' AND began_in_testing AND NOT launched_before_data_start "
              "AND NOT (path = 'moved' AND moves_that_day >= 40) AND first_day > data_through - 30 GROUP BY 1 ORDER BY 1;")])

av("creative_scaling_placements", src="public.creative_scaling_placements", domain="creative", span=("creative_scaling_placements", "first_day", "last_day"), base="creative_scaling_placements",
   purpose="Where a creative that moved to scaling scales: its scaling campaign and ad set since the move, with spend and results, in three bases.", grain="one row per (basis, family_key, campaign_id, adset_id)",
   source="public.creative_scaling_placements and its twins (etl.stage_moves)", refresh="nightly (etl service 13:00 UTC)", coverage="creatives that moved since 2026-05-06",
   caveats=[_BASIS], ren={"campaign_name": "campaign", "adset_name": "adset"}, cols={"basis": _BASIS},
   examples=[("Ad sets that took the most spend from moved creatives (Meta basis)", "SELECT campaign_name, adset_name, count(DISTINCT family_key) AS creatives, round(sum(spend)) AS spend FROM analytics.creative_scaling_placements "
              "WHERE basis = 'meta' GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 15;")])

av("creative_scaling_benchmarks", src="public.scaling_benchmarks", domain="creative", base="scaling_benchmarks",
   purpose="Benchmarks of the Test to scale page: creatives, spend, purchases and revenue by basis, product and period.", grain="one row per (basis, product_slug, day_from, day_to)",
   source="public.scaling_benchmarks", refresh="on demand", coverage="the periods the Test to scale page compares against",
   examples=[("Benchmarks", "SELECT basis, product_slug, day_from, day_to, creatives, round(revenue / NULLIF(spend, 0), 2) AS roas FROM analytics.creative_scaling_benchmarks ORDER BY day_from DESC, basis, product_slug LIMIT 20;")])

av("creative_content", src="public.family_content", domain="creative", base="family_content",
   purpose="The copy and transcript Meta actually runs for each creative family (no image or video bytes).", grain="one row per family_key", source="public.family_content (etl.assets_pull / run_daily)", refresh="nightly",
   coverage="creative families with a pulled ad", caveats=["transcript is the spoken audio for videos and can differ from the tracker's own transcript column."],
   examples=[("Video families with a transcript", "SELECT family_key, transcript_seconds, left(transcript, 120) AS transcript_start FROM analytics.creative_content WHERE is_video AND transcript IS NOT NULL ORDER BY transcript_seconds DESC NULLS LAST LIMIT 10;")])

# a plain rel(), like the Amazon views of migrations 251 / 253: the view and its comments live in migration 254, not in 230
rel("analytics.ad_transcripts", group="analytics", kind="view", like="public.creative_ad_transcripts", domain="creative", base="creative_ad_transcripts", span=("creative_ad_transcripts", "created_at::date"),
   purpose="What is said in the ad videos, taken from the real audio (speech to text), one row per ad with its tracker product, vendor, status, type and launch date. The way to ask 'which ads mention X' or 'what do the opening lines of the winners sound like'.",
   grain="one row per ad (concept_id)", source="public.creative_ad_transcripts (the transcription job) + the live tracker row of the same concept_id (public.tracker_rows, public.products)",
   refresh="when the transcription job is run (on demand on the laptop; not scheduled)",
   coverage="ads transcribed from 2026-11-13: every video on a Ready to launch Production card plus the ads transcribed in the Nov 3-5 tagging cycles (Vita Bull, Beef Liver, Boneflex); images and videos that could not be downloaded have none",
   caveats=["Speech to text as heard: brand and ingredient names can be misspelled (heme iron may come out as Heam iron). Audio is cut at 180 seconds (duration_s is the full length).",
            "41 silent videos are stored as [no speech]: has_speech is false and word_count 0. About 237 Beef Liver videos have no transcript yet, so a missing row is not an ad without speech.",
            "Join to the tracker views on concept_id (analytics.creative_tracker.concept_id) or ad_family / family_key. A concept_id can sit on more than one tracker row (a few repeat), so this view picks ONE live tracker row per ad (the lowest id); for spend and returns go through family_key to analytics.creative_tracker (metrics sit on the first row of each family) or analytics.creative_family_metrics, never by summing across this view.",
            "A transcript whose ad is no longer on the tracker keeps its row with NULL product_slug, vendor, tracker_status, ad_type and launch_date.",
            "analytics.creative_content.transcript is a different source: what Meta runs for each family. This one is the audio of the creative file the production team delivered."],
   cols={"ad_family": "Ad family of the tracker row (as typed in the tracker); NULL when the ad is not on the tracker.", "family_key": "lower(trim(ad_family)): the 'unique ad' key shared with analytics.creative_tracker and the Meta views; NULL when not on the tracker.",
         "product_slug": "vita-bull | beef-liver | boneflex | vita-belle | vital-flow; NULL when the ad is not on the tracker.", "vendor": "Vendor named on the tracker row (Madre, Victoria, Internal ...).",
         "tracker_status": "Status of the tracker row (for example 7 - Approved - Ready To Launch, Launched).", "ad_type": "VID or IMG, from the tracker.", "launch_date": "Launch date typed in the tracker (NULL when blank; undercounts the older ads).",
         "word_count": "Words in the transcript; 0 for [no speech].", "has_speech": "True when the transcript holds words (not blank and not [no speech])."},
   examples=[("Ads whose transcript mentions an ingredient", "SELECT concept_id, product_slug, vendor, tracker_status, left(transcript, 160) AS opening FROM analytics.ad_transcripts WHERE transcript ILIKE '%heme iron%' ORDER BY created_at DESC LIMIT 25;"),
             ("Most used words in the transcripts of one product", "SELECT w AS word, count(*) AS uses FROM analytics.ad_transcripts t, regexp_split_to_table(lower(regexp_replace(t.transcript, '[^a-zA-Z'' ]', ' ', 'g')), '\\s+') AS w WHERE t.has_speech AND t.product_slug = 'beef-liver' AND length(w) > 3 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;"),
             ("First sentence (hook) of each ad", "SELECT concept_id, split_part(transcript, '. ', 1) AS first_sentence FROM analytics.ad_transcripts WHERE has_speech ORDER BY created_at DESC LIMIT 25;")])

# ------------------------------------------------------------------------------------------------------------------------------- tags
av("tag_products", src="public.products", domain="tags", base="products",
   purpose="The products and the tag set each uses (tag_set 'linemap' = tagged to the Communication Overview lines, 'tracker' = the tracker's own tag columns).", grain="one row per product",
   source="public.products", refresh="on change", coverage="5 products", ren={"product_id": "id"},
   examples=[("Products", "SELECT slug, name, tag_set, active FROM analytics.tag_products ORDER BY sort_order;")])

av("tag_taxonomy_lines", src="public.taxonomy_lines", domain="tags", base="taxonomy_lines",
   purpose="The Communication Overview lines: one per ICP x pain point, with its market cluster, emotional trigger and story, per product. line_code is the code tags point at.", grain="one row per line_id",
   source="public.taxonomy_lines joined to public.products", refresh="when the taxonomy is reloaded (etl.load_taxonomy)", coverage="every line the product has",
   ren={"line_id": "id"}, cols={"product_slug": "Product the line belongs to."},
   examples=[("Lines per product and cluster", "SELECT product_slug, cluster, count(*) AS lines FROM analytics.tag_taxonomy_lines GROUP BY 1, 2 ORDER BY 1, 3 DESC;")])

av("tag_line_ingredients", src="public.line_ingredients", domain="tags", base="line_ingredients",
   purpose="The ingredients that address each taxonomy line: one row per line and ingredient with what it attacks and what it supports.", grain="one row per ingredient_id",
   source="public.line_ingredients joined to taxonomy_lines and products", refresh="when the taxonomy is reloaded", coverage="every line",
   ren={"ingredient_id": "id"}, cols={"product_slug": "Product of the line.", "line_code": "taxonomy_lines.line_code."},
   examples=[("Ingredients of one pain point", "SELECT i.product_slug, i.line_code, i.ingredient, i.attacks FROM analytics.tag_line_ingredients i JOIN analytics.tag_taxonomy_lines l USING (line_id) WHERE l.pain_point ILIKE '%hair%' ORDER BY 1, 2 LIMIT 20;")])

av("tracker_tags", src="public.ad_tags", domain="tags", span=("ad_tags", "tagged_at::date"),
   purpose="Every tag ever written to a tracker row (current and superseded), joined to the tracker row, product and taxonomy line. Filter is_current for what stands now.", grain="one row per tag_id",
   source="public.ad_tags joined to tracker_rows, products and taxonomy_lines (the extra json is left out)", refresh="when tags load or the auto-tagger runs", coverage="every live tracker row",
   caveats=["A tracker row has at most one CURRENT tag per tag_set; analytics.creative_tracker already shows the current tag of the product's own tag set.",
            "confidence / method say how sure the tagger was: low-confidence and copy-only tags are weaker evidence."],
   ren={"tag_id": "id"}, cols={"concept_id": "Creative Tracker concept id of the tagged row.", "family_key": "Tracker ad family key (join to creative_tracker / creative_family_metrics).",
                              "product_slug": "Product of the tracker row.", "line_code": "taxonomy_lines.line_code the tag points at (NULL for tracker-set tags)."},
   examples=[("Current line tags per product and confidence", "SELECT product_slug, confidence, count(*) AS tags FROM analytics.tracker_tags WHERE is_current AND tag_set = 'linemap' GROUP BY 1, 2 ORDER BY 1, 2;")])

av("tracker_hooks", src="public.ad_hooks", domain="tags", span=("ad_hooks", "extracted_at::date"),
   purpose="The hook of each tracker row: the first spoken line and the first on-screen text, joined to the tracker row and product.", grain="one row per tracker_row_id",
   source="public.ad_hooks joined to tracker_rows and products", refresh="when hooks are reloaded (etl.load_hooks)", coverage="the current tracker rows that had something readable (a silent video or an ad with no copy has no hook)",
   caveats=["hook_source says where the hook came from (video transcript, image, copy). Rows with nothing readable have no hook."],
   cols={"concept_id": "Creative Tracker concept id.", "family_key": "Tracker ad family key.", "product_slug": "Product of the tracker row."},
   examples=[("Hooks of the 10 biggest launched families (Meta basis)", "SELECT h.product_slug, h.concept_id, h.hook, round(m.spend_launch) AS spend_launch FROM analytics.tracker_hooks h JOIN analytics.creative_family_metrics m "
              "ON m.tracker_row_id = h.tracker_row_id AND m.basis = 'meta' WHERE m.is_first_row ORDER BY m.spend_launch DESC LIMIT 10;")])

# ------------------------------------------------------------------------------------------------------------------------------- priorities
av("priorities_snapshots", src="public.creative_priorities_snapshots", domain="priorities", span=("creative_priorities_snapshots", "week_start"),
   purpose="One row per weekly creative priorities snapshot: when it was built, the data it used and how many rows it holds.", grain="one row per week_start (the Sunday, Pacific)",
   source="public.creative_priorities_snapshots without the json blobs (they are flattened into the other priorities_* views)", refresh="Sundays in the nightly etl (etl.creative_priorities)",
   coverage="from the first week the page ran",
   cols={"row_count": "Number of rows in the snapshot (every line plus manual and proposal rows)."},
   examples=[("Snapshots", "SELECT week_start, data_through, row_count FROM analytics.priorities_snapshots ORDER BY week_start DESC;")])

av("priorities_thresholds", src="public.creative_priorities_snapshots", domain="priorities", span=("creative_priorities_snapshots", "week_start"), base="creative_priorities_snapshots",
   purpose="Every threshold and weight the week was scored with (HIT_SPEND, TOP_SPEND, JUDGED_SPEND, MIN_LINE_SPEND, the score weights ...), one row per (week, name).", grain="one row per (week_start, name)",
   source="public.creative_priorities_snapshots.thresholds, flattened", refresh="Sundays", coverage="every snapshot",
   caveats=["value is text: cast it. Scoring counts a hit at HIT_SPEND = $1,000; the displayed hit rate uses TOP_SPEND = $10,000 (etl/creative_priorities.py)."],
   cols={"name": "Constant name as in etl/creative_priorities.py.", "value": "Its value for that week, as text."},
   examples=[("The hit-rate constants of the latest week", "SELECT name, value FROM analytics.priorities_thresholds WHERE week_start = (SELECT max(week_start) FROM analytics.priorities_snapshots) AND name IN ('HIT_SPEND', 'TOP_SPEND', 'JUDGED_SPEND', 'MATURE_DAYS');")])

av("priorities_baselines", src="public.creative_priorities_snapshots", domain="priorities", span=("creative_priorities_snapshots", "week_start"), base="creative_priorities_snapshots",
   purpose="The product averages each line is compared with (ROAS, CPA, hit rate, spend, funnel counts), one numeric row per (week, product, metric).", grain="one row per (week_start, product_slug, metric)",
   source="public.creative_priorities_snapshots.baselines, numeric entries only", refresh="Sundays", coverage="every snapshot",
   caveats=["Metrics include roas, cpa, hit_rate (scoring basis, $1,000 over mature tested ads) and the funnel counts; see priorities_concepts for the per-line figures."],
   cols={"week_start": "The snapshot's Sunday.", "product_slug": "Product the baseline is for.", "metric": "Baseline metric name as in the snapshot.", "value": "Its numeric value."},
   examples=[("Product ROAS and CPA baselines of the latest week", "SELECT product_slug, metric, value FROM analytics.priorities_baselines WHERE week_start = (SELECT max(week_start) FROM analytics.priorities_snapshots) AND metric IN ('roas', 'cpa', 'hit_rate') ORDER BY 1, 2;")])

av("priorities_concepts", src="public.creative_priorities_snapshots", domain="priorities", span=("creative_priorities_snapshots", "week_start"), base="creative_priorities_snapshots",
   purpose="Every row of every weekly priorities snapshot, flattened: the line (cluster, ICP, pain point), its action section and status, score, confidence and the numbers behind it (Meta-reported, with Upstack new-customer ROAS beside them).",
   grain="one row per (week_start, row of the snapshot)", source="public.creative_priorities_snapshots.rows, flattened with the numeric fields typed", refresh="Sundays", coverage="every snapshot",
   caveats=["Two hit rates exist. hit_rate / hit_rate_index = SCORING: share of mature (>= 14 days old) tested ads that reached $1,000, shrunk toward the product average; the CSV export's hit_rate columns are this one. "
            "DISPLAY (what Josh calls a hit): ads_10k_plus_spend / ads_launched, ads that reached $10,000 since launch out of every launched ad. Name which one you mean.",
            "Numbers are Meta-reported (roas = purchase value / spend); nc_roas is Upstack new-customer ROAS. ROAS is NULL under $100 spend. section / status use the current vocabulary (older snapshots are stored in the old words)."],
   cols={"week_start": "The snapshot's Sunday.", "product_slug": "vita-bull | beef-liver | boneflex (rows of manual concepts keep their typed product).",
         "section": "Action section the row sits in (make more, revive, test, stop, hold, add to sheet, proposed, competitor, everything else).", "status": "Line status (WORKING, RISING, FADED, COLLAPSED, TOO EARLY, UNTESTED, NOT WORKING, AVERAGE ...).",
         "rank": "Rank inside its product and section.", "line_code": "taxonomy_lines.line_code (NULL for manual and proposed rows).", "cluster": "Market cluster.", "icp": "ICP.", "pain_point": "Pain point.",
         "emotional_trigger": "Emotional trigger of the line.", "story": "Story of the line.", "concepts": "New concepts to brief for the row this week.", "score": "0-100 iterate score (35% economics, 20% hit rate, 20% room, 15% momentum, 10% size).",
         "confidence": "high | medium | low.", "flags": "Risk flags, joined with '; ' (thin sample, weak tags, few ads, Upstack new-customer ROAS below product average).", "why": "One-paragraph reason the row was chosen.",
         "is_manual": "true for a concept typed by hand on the page.", "spend": "Since-launch spend of the line, Meta, dollars.", "purchases": "Since-launch Meta-reported purchases.", "roas": "Since-launch Meta ROAS (NULL under $100 spend).",
         "cpa": "Since-launch cost per purchase.", "roas_index": "Shrunk since-launch ROAS / the product's ROAS.", "ads": "Ads of the line that spent.", "judged": "Ads that spent $100 or more.", "hits": "Mature judged ads that reached $1,000 (scoring hits).",
         "mature": "Judged ads at least 14 days old (the scoring hit-rate denominator).", "hit_rate": "SCORING hit rate: hits / mature (the $1,000 threshold).", "hit_rate_index": "Shrunk hit rate / the product's.",
         "spend_30d": "Spend in the 30 days ending at data_through.", "roas_30d": "ROAS over those 30 days.", "spend_14d": "Spend in the 14 days ending at data_through.", "roas_14d": "ROAS over those 14 days (NULL under $100).",
         "nc_roas": "Upstack new-customer ROAS since launch.", "nc_cpa": "Upstack new-customer CPA since launch.", "nc_purchases": "Upstack new customers credited since launch.", "live_ads": "Ads of the line that spent in the last days.",
         "ads_launched": "DISPLAY funnel: ads of the line launched (tracker rows with a launch date or any spend).", "ads_under_100_spend": "Launched ads under $100 spend.", "ads_100_plus_spend": "Launched ads at $100 or more.",
         "ads_10k_plus_spend": "Launched ads at $10,000 or more (the display hits)."},
   examples=[("Lines to iterate on this week, by product", "SELECT product_slug, rank, cluster, icp, pain_point, status, score, round(spend) AS spend, roas FROM analytics.priorities_concepts "
              "WHERE week_start = (SELECT max(week_start) FROM analytics.priorities_snapshots) AND section IN ('iterate', 'make more') ORDER BY product_slug, rank LIMIT 30;"),
             ("Display hit rate per product (ads reaching $10K / launched ads) over the weeks", "SELECT week_start, product_slug, sum(ads_10k_plus_spend) AS hits, sum(ads_launched) AS launched FROM analytics.priorities_concepts "
              "WHERE ads_launched IS NOT NULL GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT 12;")])

av("priorities_manual_concepts", src="public.creative_priorities_manual", domain="priorities", span=("creative_priorities_manual", "created_at::date"),
   purpose="Concepts typed in by hand on the priorities page (not scored): product, section, line, why now and status.", grain="one row per manual_id", source="public.creative_priorities_manual",
   refresh="on demand (edited by a person)", coverage="every manual concept", ren={"manual_id": "id"},
   examples=[("Open manual concepts", "SELECT manual_id, product, section, cluster, icp, pain_point, status FROM analytics.priorities_manual_concepts WHERE done_at IS NULL ORDER BY created_at DESC;")])

# ------------------------------------------------------------------------------------------------------------------------------- production
av("production_assignments", src="public.creative_priorities_assignments", domain="production", span=("creative_priorities_assignments", "for_week"),
   purpose="One concept given to one vendor to make for one week: the line, the brief and creative links, the timestamp of each step and the tracker concepts it became.", grain="one row per assignment_id",
   source="public.creative_priorities_assignments without created_by / updated_by (who made a change is not exposed)", refresh="on demand (the production board and the nightly etl.assignment_tags)", coverage="from the day production tracking started",
   caveats=["stage_from_assignment reads only the assignment's own fields; the app also takes the furthest stage its linked tracker rows show (join tracker_concept_ids to creative_tracker.concept_id and read status).",
            "vendor_note is the note the vendor sees; note is internal."],
   ren={"assignment_id": "id"}, cols={"stage_from_assignment": "dropped | launched | ready | creative_in | brief_sent | assigned, from dropped_at, launched_at, ready_at, creative_url and brief_url in that order."},
   examples=[("Open assignments by vendor and week", "SELECT vendor, for_week, stage_from_assignment, count(*) AS assignments FROM analytics.production_assignments WHERE stage_from_assignment NOT IN ('launched', 'dropped') GROUP BY 1, 2, 3 ORDER BY 2 DESC, 1;")])

av("production_formats", src="public.creative_formats", domain="production", base="creative_formats",
   purpose="The creative formats (static, short video, ...) an assignment can carry.", grain="one row per format_code", source="public.creative_formats", refresh="on change", coverage="a handful of rows",
   ren={"format_code": "code"},
   examples=[("Formats", "SELECT format_code, name, length FROM analytics.production_formats WHERE active ORDER BY sort;")])

# ------------------------------------------------------------------------------------------------------------------------------- daily story
av("daily_story", src="public.daily_story", domain="story", span=("daily_story", "day"), purpose="The daily story: the short explanation of why each day's performance was what it was, with confidence labels and what the database cannot see.", grain="one row per day",
   source="public.daily_story", refresh="nightly", coverage="2026-05-06 through yesterday",
   examples=[("Latest story", "SELECT day, headline, story FROM analytics.daily_story ORDER BY day DESC LIMIT 1;")])
av("daily_story_facts", src="public.daily_story_facts", domain="story", span=("daily_story_facts", "day"), purpose="The JSON facts each daily story is written from (metrics vs same-weekday baseline, decomposition, cuts, ledger, sections).", grain="one row per day",
   source="public.daily_story_facts", refresh="nightly", coverage="2026-05-06 through yesterday",
   examples=[("Headline decomposition of a day", "SELECT day, jsonb_path_query(facts, '$.decomposition.terms[*].name') AS term FROM analytics.daily_story_facts WHERE day = (SELECT max(day) FROM analytics.daily_story_facts) LIMIT 10;")])
av("daily_story_patterns", src="public.daily_story_patterns", domain="story", span=("daily_story_patterns", "day"), purpose="Pattern memory: change types seen each day and the next 1-3 days' outcomes against same-weekday baselines.", grain="one row per (day, change_type)",
   source="public.daily_story_patterns", refresh="nightly", coverage="2026-05-06 through yesterday",
   examples=[("Days with a large email send", "SELECT day, magnitude_bucket, out_nc_roas FROM analytics.daily_story_patterns WHERE change_type = 'big_email_send' ORDER BY day DESC LIMIT 10;")])
av("daily_story_pattern_scoreboard", src="public.daily_story_pattern_scoreboard", domain="story", base="daily_story_patterns", purpose="Which change types have preceded rises or falls, with sample sizes and the base rate (correlation, not cause).",
   grain="one row per (change_type, magnitude_bucket, metric), n >= 6", source="public.daily_story_pattern_scoreboard", refresh="always current (view)", coverage="2026-05-06 through yesterday",
   examples=[("Strongest NC ROAS patterns", "SELECT change_type, magnitude_bucket, n, rises, falls, round((rise_lift)::numeric, 2) AS rise_lift FROM analytics.daily_story_pattern_scoreboard WHERE metric = 'nc_roas' ORDER BY abs(rise_lift) DESC LIMIT 10;")])
av("daily_story_metric_registry", src="public.daily_story_metrics", domain="story", span=None, purpose="The daily story's metric registry: every daily metric, its basis, formula and source view.", grain="one row per metric_key",
   source="public.daily_story_metrics", refresh="nightly", coverage="every metric the story computes",
   examples=[("Metrics by group", "SELECT metric_group, count(*) AS metrics FROM analytics.daily_story_metric_registry GROUP BY 1 ORDER BY 1;")])

# ------------------------------------------------------------------------------------------------------------------------------- competitors
av("competitor_brands", src="public.competitor_brands", domain="competitors", base="competitor_brands",
   purpose="The competitor brands tracked in the Meta Ad Library and when each was last read.", grain="one row per brand", source="public.competitor_brands without last_error", refresh="Sundays (etl.competitor_ads)",
   coverage="every tracked brand",
   examples=[("Brands not read successfully this week", "SELECT brand, product_type, last_ok_at, last_ad_count FROM analytics.competitor_brands WHERE active AND (last_ok_at IS NULL OR last_ok_at < now() - interval '8 days') ORDER BY brand;")])

av("competitor_ads", src="public.competitor_ads", domain="competitors", span=("competitor_ads", "week_start", "seen_on"),
   purpose="Weekly snapshot of each competitor ad found in the Meta Ad Library: format, copy, hook, landing page and how long it has been running.", grain="one row per (week_start, library_id)", source="public.competitor_ads",
   refresh="Sundays (etl.competitor_ads)", coverage="from the first week the tracker ran",
   caveats=["Running a long time is not proven performance: the Ad Library shows no spend or results."],
   examples=[("Longest-running ads of the latest week", "SELECT brand, format, days_running, left(hook, 100) AS hook FROM analytics.competitor_ads WHERE week_start = (SELECT max(week_start) FROM analytics.competitor_ads) ORDER BY days_running DESC NULLS LAST LIMIT 15;")])

av("competitor_concepts", src="public.competitor_concepts", domain="competitors", span=("competitor_ads", "week_start", "seen_on"), base="competitor_ads",
   purpose="Competitor ads grouped into concepts per week and mapped to our product, line, cluster, ICP and pain point (or flagged as a new angle).", grain="one row per (week_start, brand, concept_key)",
   source="public.competitor_concepts (a view over competitor_ads and competitor_concept_class)", refresh="Sundays", coverage="every week with a snapshot",
   caveats=["The mapping to our lines is a model's classification (confidence says how sure); new-angle rows are concepts that fit no existing line."],
   examples=[("New angles competitors are running, latest week", "SELECT brand, new_angle_label, days_running, variant_count FROM analytics.competitor_concepts WHERE week_start = (SELECT max(week_start) FROM analytics.competitor_concepts) AND is_new_angle ORDER BY days_running DESC NULLS LAST LIMIT 15;")])

# ------------------------------------------------------------------------------------------------------------------------------- catalog and glossary
BRAIN_VIEWS.update({"analytics.catalog", "analytics.metric_glossary"})
rel("analytics.catalog", group="analytics", kind="view", domain="pipeline", span=None, base=None,
    purpose="One row per analytics view: its domain, what it is, its grain, the first and last day it covers where it has days, and a row-count estimate. Start here to find which view answers a question.",
    grain="one row per analytics view (this view itself is listed too)", source="generated from docs/schema/descriptions.py into migration 230 (descriptions, domains) and the first / last day of each view's base table; row_estimate = pg_class.reltuples",
    refresh="first_day / last_day read the base tables live; the descriptions change only with a migration", coverage="every analytics view",
    caveats=["row_estimate is Postgres's planner estimate of the base table (NULL until the table is first analysed), not a count; run count(*) when you need the exact number.",
             "first_day / last_day are NULL for views without a day axis (dimensions, definitions, scenarios). Read data_coverage for per-source freshness of the marketing data."],
    cols={"view_name": "Name of the view in the analytics schema.", "domain": "marketing | attribution | orders | creative | tags | priorities | production | projection | inventory | cohorts | pacing | competitors | pipeline.",
          "description": "What the view is (its purpose text).", "grain": "What one row is.", "first_day": "First day the view's base table holds (NULL when not a time series).",
          "last_day": "Last day the base table holds.", "row_estimate": "Planner row-count estimate of the base table."},
    examples=[("All views of one domain", "SELECT view_name, grain, first_day, last_day FROM analytics.catalog WHERE domain = 'creative' ORDER BY view_name;"),
              ("How many views per domain", "SELECT domain, count(*) AS views FROM analytics.catalog GROUP BY 1 ORDER BY 1;")])

rel("analytics.metric_glossary", group="analytics", kind="view", domain="pipeline", span=None, base=None,
    purpose="The canonical definition of every headline metric (ROAS, CPA, NC ROAS, CPM, frequency, hit rate, payback, ...): formula, the view and columns that hold the inputs, the default basis, the other bases and the traps. The same rows as the 'metrics' key of docs/schema.json.",
    grain="one row per metric name", source="generated from the METRICS list in docs/schema/descriptions.py into migration 230; every formula was taken from the code (the file is named in source or traps)",
    refresh="changes only with a migration", coverage="every metric of the glossary (see the Metrics section of docs/DATA_SCHEMA.md)",
    caveats=["other_bases and traps are text arrays: unnest them or read them whole. Read the default basis before quoting a number and name the basis in the answer."],
    cols={"name": "Machine name, e.g. nc_roas.", "label": "Display name.", "formula": "The formula in plain words and column names.", "source": "analytics.<view>.<column(s)> that hold the inputs.",
          "default_basis": "The basis to use unless asked otherwise.", "other_bases": "The other bases that exist for the metric.", "traps": "Ways the metric is read wrongly, with the code file the rule comes from.",
          "domain": "Domain of the metric (same list as analytics.catalog)."},
    examples=[("One metric", "SELECT label, formula, source, default_basis, other_bases, traps FROM analytics.metric_glossary WHERE name = 'total_nc_roas';"),
              ("All metrics by domain", "SELECT domain, name, label FROM analytics.metric_glossary ORDER BY domain, name;")])


# =====================================================================================================================================
# Amazon data layer (migrations 250 tables, 251 views): ExpandFi merchant 1452, filled by the feeds service (etl/feeds/amazon_feeds.py); docs/AMAZON_DATA.md
# =====================================================================================================================================

_AMZ_REFRESH = "feeds service 21:00 UTC (python -m etl.feeds run), trailing {w}; each run replaces its window, an empty pull writes nothing"
_AMZ_SRC = "etl.feeds {feed} (ExpandFi warehouse, merchant 1452: {tables})"
_AMZ_PRODUCT = {"product_slug": "vita-bull | beef-liver | boneflex | vita-belle | vital-flow; NULL when the ASIN (or the campaign name's ASIN) is not an Amazon.com ASIN of amazon_asin_products.",
                "product_name": "Display name of product_slug (Vita Bull, Beef Liver, Boneflex, Vita Belle, Vital Flow)."}
_AMZ_LOADED = {"loaded_at": "When the row was loaded."}
_AMZ_WINDOWS = {
    "sales_1d": "Ad-attributed sales, dollars, 1-day click window. Sponsored Products (v3 report) only; NULL for every other row.",
    "sales_7d": "Ad-attributed sales, dollars, 7-day click window. Sponsored Products v3 only; NULL otherwise.",
    "sales_14d": "Ad-attributed sales, dollars, 14-day click window. Sponsored Products v3 only; NULL otherwise. Matures for 14 days after the day.",
    "sales_30d": "Ad-attributed sales, dollars, 30-day click window. Sponsored Products v3 only; NULL otherwise.",
    "purchases_1d": "Ad-attributed purchases, 1-day window. Sponsored Products v3 only; NULL otherwise.",
    "purchases_7d": "Ad-attributed purchases, 7-day window. Sponsored Products v3 only; NULL otherwise.",
    "purchases_14d": "Ad-attributed purchases, 14-day window. Sponsored Products v3 only; NULL otherwise.",
    "purchases_30d": "Ad-attributed purchases, 30-day window. Sponsored Products v3 only; NULL otherwise.",
    "units_1d": "Units sold on ad clicks, 1-day window. Sponsored Products v3 only; NULL otherwise.",
    "units_7d": "Units sold on ad clicks, 7-day window. Sponsored Products v3 only; NULL otherwise.",
    "units_14d": "Units sold on ad clicks, 14-day window. Sponsored Products v3 only; NULL otherwise.",
    "units_30d": "Units sold on ad clicks, 30-day window. Sponsored Products v3 only; NULL otherwise.",
}
_AMZ_ADS_NOTES = [
    "ad_type comes from the report type. Rows from the older v1 report that ExpandFi files under spCampaigns also hold Sponsored Brands, Sponsored Display and Full-Funnel campaigns, so they read 'sp'.",
    "Amazon has no ad-attributed revenue on one basis: sales_reported is Sponsored Products 14-day sales and the single reported sales figure of the other types. Do not add it to Meta or Google revenue.",
    "The 1/7/14/30-day columns exist for Sponsored Products v3 only; older generations store zeros there, so they are NULL here. NULL is not zero.",
]

rel("public.amazon_asin_products", group="amazon", kind="table",
    purpose="Amazon.com ASIN to SKU to product map (ExpandFi asin_skus), the join key of every other Amazon table.",
    grain="one row per (asin, sku)", source=_AMZ_SRC.format(feed="amazon_asin_products", tables="asin_skus, channel Amazon.com"), refresh="feeds service 21:00 UTC, full refresh every run",
    coverage="8 ASINs for 5 products (Vital Flow has no Amazon ASIN)",
    caveats=["Product comes from the SKU prefix (LV-VBULL, LV-BLC, LV-BFLEX, LV-VBELLE), the listing title as fallback. Aggregates only."],
    cols={"asin": "Amazon ASIN.", "sku": "Seller SKU.", **_AMZ_PRODUCT, "friendly_name": "Listing title (first 200 characters).", "refreshed_on": "Pacific day of the refresh.", **_AMZ_LOADED})

rel("public.amazon_ads_campaign_daily", group="amazon", kind="table",
    purpose="Amazon Ads (Sponsored Products, Brands, Brand video, Display) by day, campaign and ad group, de-duplicated v3 > v2 > v1 per campaign-day; the daily spend sum equals proj_feed_amazon.ad_spend.",
    grain="one row per (day, marketplace, campaign_id, ad_group_id, ad_type); ad_group_id is '' for types that report no ad group",
    source=_AMZ_SRC.format(feed="amazon_ads_campaign_daily", tables="amazon_ads_campaign_data"), refresh=_AMZ_REFRESH.format(w="21 days"),
    coverage="2025-12-30 (Sponsored Products v3) to yesterday; Sponsored Display from 2026-01-29, Sponsored Brands from 2026-02-03. Rows with no spend, impressions, clicks or sales are not stored.",
    caveats=_AMZ_ADS_NOTES + ["Campaigns carry no product in the report: product_slug is the ASIN the campaign name contains (about 88% of spend); campaigns such as 'All Products' and 'Full-Funnel' have NULL."],
    cols={"day": "Calendar day of the report (Amazon Ads account day).", "marketplace": "Ads marketplace code; rows from the v1 report carry none and are stored as 'US'.",
          "campaign_id": "Amazon Ads campaign id.", "ad_group_id": "Ad group id (Sponsored Products); '' when the report has none.",
          "ad_type": "sp (Sponsored Products) | sb (Sponsored Brands) | sbv (Sponsored Brands video) | sd (Sponsored Display), from api_report_type.",
          "api_version": "Report generation kept for this campaign-day: v3, v2 or v1.", "campaign_name": "Campaign name that day.", "ad_group_name": "Ad group name that day.",
          "campaign_status": "Campaign status that day (not on every report type).", "budget_amount": "Campaign daily budget in the account currency (not on every report type).",
          "budget_type": "Budget type, e.g. DAILY.", "bidding_strategy": "Campaign bidding strategy (Sponsored Products v3 only).", **_AMZ_PRODUCT,
          "spend": "Ad spend, US dollars (cost_usd).", "impressions": "Ad impressions.", "clicks": "Ad clicks.", **_AMZ_WINDOWS,
          "sales": "The report's bare attributed sales, dollars: the single reported figure for Sponsored Brands and Display, an aggregate for the v1 report.",
          "purchases": "The report's bare attributed purchases.", "units_sold": "The report's bare units sold.",
          "ntb_purchases": "New-to-brand purchases (Sponsored Brands and Display; populated for Display only, 0 for Brands).", "ntb_sales": "New-to-brand sales, dollars.",
          "ntb_units": "New-to-brand units.", "video_complete_views": "Video complete views (Sponsored Brands).", "video_5s_views": "Video 5-second views.",
          "video_q1_views": "Video views to the first quartile.", "video_midpoint_views": "Video views to the midpoint.", "video_q3_views": "Video views to the third quartile.",
          "brand_searches": "Branded searches after the ad (Sponsored Brands).",
          "sales_reported": "Attributed sales on the one basis available per type: Sponsored Products v3 sales_14d, otherwise the bare sales. Use it for a total-ads return on spend.", **_AMZ_LOADED})

rel("public.amazon_ads_asin_daily", group="amazon", kind="table",
    purpose="Amazon Ads advertised products (Sponsored Products and Display) by day, campaign, ad group and ASIN: which product each ad dollar went to.",
    grain="one row per (day, marketplace, campaign_id, ad_group_id, asin, ad_type)", source=_AMZ_SRC.format(feed="amazon_ads_asin_daily", tables="amazon_ads_raw_data v3 spAdvertisedProduct and sdAdvertisedProduct"),
    refresh=_AMZ_REFRESH.format(w="21 days"), coverage="2026-01-04 (Sponsored Products) / 2026-01-29 (Display) to yesterday",
    caveats=["v3 only: the v1 advertised-product reports are one report stored twice under two names. Sponsored Products v3 spend ties to amazon_ads_campaign_daily; there is no Sponsored Brands ASIN grain anywhere.",
             "The 1/7/14/30-day columns are Sponsored Products only (NULL for Display); Display reports its single attributed figure in sales and purchases."],
    cols={"day": "Calendar day of the report.", "marketplace": "Ads marketplace code.", "campaign_id": "Amazon Ads campaign id.", "ad_group_id": "Ad group id.", "asin": "Advertised ASIN.",
          "ad_type": "sp | sd.", "sku": "Advertised SKU.", "campaign_name": "Campaign name that day.", "ad_group_name": "Ad group name that day.", **_AMZ_PRODUCT,
          "spend": "Ad spend, US dollars.", "impressions": "Ad impressions.", "clicks": "Ad clicks.", **_AMZ_WINDOWS,
          "sales": "Display only: the single reported attributed sales, dollars (sales_aggregate). NULL for Sponsored Products.", "purchases": "Display only: the single reported purchases. NULL for Sponsored Products.",
          "sales_reported": "Sponsored Products sales_14d, Display bare sales.", **_AMZ_LOADED})

_SQP_COLS = {
    "period_start": "First day of the period (Sunday for a week, the 1st for a month).", "period_end": "Last day of the period.",
    "marketplace_id": "Amazon marketplace id (ATVPDKIKX0DER = US, A1AM78C64UM0Y8 = MX).", "marketplace": "US | MX | CA | BR from marketplace_id.", "asin": "Our ASIN the shares belong to.",
    "query_key": "The search query lower-cased, trimmed, runs of spaces collapsed to one: the join key to other query tables.", "search_query": "The query text as Amazon reported it.",
    **_AMZ_PRODUCT, "is_branded": "True when the query names the brand or a product (Lonvera, Vita / Vital Bull, Vita / Vital Belle, Vital Flow, Bone Flex / Boneflex); one rule in etl/feeds/amazon_feeds.py.",
    "search_query_score": "Amazon's relative popularity score of the query among our ASIN's queries (1 = most searched).", "search_query_volume": "MARKET total: searches for the query on Amazon in the period. Repeated on every ASIN row of the query.",
    "total_impressions": "MARKET total: impressions of all products for the query (organic and sponsored). Repeated on every ASIN row: never SUM across ASINs, use MAX.",
    "asin_impressions": "Impressions of our ASIN for the query (organic and sponsored).", "asin_impression_share": "Our ASIN's share of the query's impressions, percent 0-100.",
    "total_clicks": "MARKET total clicks for the query (repeated per ASIN row: MAX).", "total_click_rate": "Market clicks per search, percent 0-100.", "asin_clicks": "Clicks on our ASIN.",
    "asin_click_share": "Our ASIN's share of the query's clicks, percent 0-100.", "total_median_click_price": "Market median price of clicked products, dollars.", "asin_median_click_price": "Median price of our ASIN's clicks, dollars.",
    "total_cart_adds": "MARKET total cart adds for the query (repeated per ASIN row: MAX).", "total_cart_add_rate": "Market cart adds per search, percent 0-100.", "asin_cart_adds": "Cart adds of our ASIN.",
    "asin_cart_add_share": "Our ASIN's share of the query's cart adds, percent 0-100.", "total_median_cart_add_price": "Market median price of carted products, dollars.", "asin_median_cart_add_price": "Median price of our ASIN's cart adds, dollars.",
    "total_purchases": "MARKET total purchases for the query (repeated per ASIN row: MAX).", "total_purchase_rate": "Market purchases per search, percent 0-100.", "asin_purchases": "Purchases of our ASIN (search-origin, same day).",
    "asin_purchase_share": "Our ASIN's share of the query's purchases, percent 0-100: the best outcome proxy for organic rank.", "total_median_purchase_price": "Market median purchase price, dollars.", "asin_median_purchase_price": "Median price of our ASIN's purchases, dollars.",
    **_AMZ_LOADED}
_SQP_CAVEATS = [
    "SUM vs MAX: total_* columns and search_query_volume are market totals repeated on every ASIN row of a query, so SUM across ASINs overstates them (5 ASIN rows = 5x): use MAX per query and period; to total OUR side SUM the asin_* columns.",
    "Top-100 cap: Amazon reports only about the top 100 queries per ASIN and period. A query that is absent is not in that list, not zero, and a query dropping out looks like a share collapse.",
    "Shares are percent 0-100, not fractions (2.95 = 2.95 percent).",
    "Impressions include sponsored placements: our own ads count in asin_impressions and the shares. Purchases are search-origin and same-day and will not tie to orders.",
    "No organic rank position exists anywhere in the data; the click, cart add and purchase shares are the proxy.",
    "Never sum weekly rows into a month or compare a week with a month: periods overlap in meaning and the query lists differ.",
    "Duplicate source rows (restated re-pulls) are collapsed to the latest; the newest week or month can still be partial for a week or two."]

rel("public.amazon_sqp_weekly", group="amazon", kind="table",
    purpose="Amazon Brand Analytics Search Query Performance by week: for each search query and each of our ASINs, the query's market size and our share of impressions, clicks, cart adds and purchases (the organic search signal).",
    grain="one row per (period_start = Sunday, marketplace_id, asin, query_key)", source=_AMZ_SRC.format(feed="amazon_sqp_weekly", tables="brand_analytics_search_query_performances, period WEEK"),
    refresh=_AMZ_REFRESH.format(w="6 weeks") + "; a week lands about 5 days after its Saturday", coverage="weeks from 2025-07-13 to the last landed week; 8 ASINs, mostly US",
    caveats=_SQP_CAVEATS, cols=_SQP_COLS)
rel("public.amazon_sqp_monthly", group="amazon", kind="table", like="public.amazon_sqp_weekly",
    purpose="Amazon Brand Analytics Search Query Performance by month: same columns as amazon_sqp_weekly, one period per calendar month.",
    grain="one row per (period_start = 1st of the month, marketplace_id, asin, query_key)", source=_AMZ_SRC.format(feed="amazon_sqp_monthly", tables="brand_analytics_search_query_performances, period MONTH"),
    refresh=_AMZ_REFRESH.format(w="3 months") + "; a month lands about 2 weeks after it ends", coverage="months from 2025-06 to the last landed month",
    caveats=_SQP_CAVEATS, cols={})

rel("public.amazon_orders_asin_daily", group="amazon", kind="table",
    purpose="Actual Amazon.com orders by day, ASIN and channel (Amazon.com and Amazon.com B2B): units, orders, revenue and new customers, on the same basis as proj_feed_amazon.",
    grain="one row per (day, asin, sales_channel)", source=_AMZ_SRC.format(feed="amazon_orders_asin_daily", tables="sales joined to customers"),
    refresh=_AMZ_REFRESH.format(w="21 days"), coverage="2024-09-24 to about 2 days ago (late orders post for about two weeks)",
    caveats=["Purchase-date basis. New customer = the order is on the customer's first purchase day (customers.cohort_day), the rule of proj_feed_amazon; orders without a customer row are excluded, as there.",
             "Daily units and revenue sums equal proj_feed_amazon. orders counts distinct orders per ASIN, so an order with two ASINs counts under both: do not sum orders across ASINs.",
             "Revenue is total_amount_usd (tax and shipping as Amazon reports the line), before refunds."],
    cols={"day": "Purchase day (Pacific, as ExpandFi stores it).", "asin": "ASIN of the order line ('' when the line has none).", "sales_channel": "Amazon.com | Amazon.com B2B.", **_AMZ_PRODUCT,
          "orders": "Distinct orders with this ASIN that day.", "units": "Units shipped.", "revenue": "Order line revenue, US dollars.", "nc_orders": "Orders placed by a customer on their first purchase day.",
          "nc_revenue": "Revenue of those orders, dollars.", **_AMZ_LOADED})

rel("public.amazon_traffic_daily", group="amazon", kind="table",
    purpose="Amazon.com account traffic by day: sessions, page views and buy box share (Sales and Traffic report), with ordered units and sales as Amazon counts them.",
    grain="one row per (day, marketplace_id); US only", source=_AMZ_SRC.format(feed="amazon_traffic_daily", tables="sales_and_traffic_reports, PARENT rows"),
    refresh=_AMZ_REFRESH.format(w="14 days"), coverage="2024-04-07 to yesterday",
    caveats=["Account level, not by ASIN (each ASIN row of the source repeats the account total). The latest restatement of each day is kept; recent days move for about a week.",
             "Percentages are 0-100. Ordered units and sales are Amazon's ordered-product figures, not the shipped orders of amazon_orders_asin_daily."],
    cols={"day": "Report day.", "marketplace_id": "ATVPDKIKX0DER (US).", "sessions": "Sessions (browser plus app).", "browser_sessions": "Browser sessions.", "mobile_app_sessions": "Mobile app sessions.",
          "page_views": "Page views.", "buy_box_pct": "Page views with our offer in the buy box, percent 0-100.", "unit_conversion_pct": "Units ordered per session, percent 0-100.",
          "units_ordered": "Units ordered (Amazon's ordered basis).", "ordered_product_sales": "Ordered product sales, dollars.", "total_order_items": "Order items.",
          "sessions_b2b": "Business-customer sessions.", "page_views_b2b": "Business-customer page views.", "buy_box_pct_b2b": "Business-customer buy box share, percent 0-100.",
          "source_updated_at": "When ExpandFi last restated this day.", **_AMZ_LOADED})

rel("public.amazon_market_baskets", group="amazon", kind="table",
    purpose="Amazon Brand Analytics market baskets by week: the other products bought together with each of our ASINs.",
    grain="one row per (week_start, marketplace_id, asin, purchased_with_asin)", source=_AMZ_SRC.format(feed="amazon_market_baskets", tables="brand_analytics_market_baskets, period WEEK"),
    refresh=_AMZ_REFRESH.format(w="6 weeks"), coverage="weeks from 2026-06-28 (US; one MX week)",
    caveats=["combination_pct is the percent (0-100) of orders containing our ASIN that also contained the other ASIN; only the top few companions are reported."],
    cols={"week_start": "Sunday starting the week.", "week_end": "Saturday ending the week.", "marketplace_id": "Amazon marketplace id.", "asin": "Our ASIN.", **_AMZ_PRODUCT,
          "purchased_with_asin": "ASIN bought in the same order.", "purchased_with_slug": "Product slug when the companion is one of ours, else NULL.", "purchased_with_rank": "Rank of the companion (1 = most common).",
          "combination_pct": "Percent 0-100 of orders with our ASIN that also held the companion.", **_AMZ_LOADED})

rel("public.amazon_repeat_purchases", group="amazon", kind="table",
    purpose="Amazon Brand Analytics repeat purchase behaviour by week and ASIN: orders, unique customers and the share that are repeat customers.",
    grain="one row per (week_start, marketplace_id, asin)", source=_AMZ_SRC.format(feed="amazon_repeat_purchases", tables="brand_analytics_repeat_purchases, period WEEK"),
    refresh=_AMZ_REFRESH.format(w="6 weeks"), coverage="weeks from 2026-06-28 (US and MX)",
    caveats=["Percentages are 0-100. Amazon computes repeat over its own look-back window, so the figures do not tie to the cohort tables."],
    cols={"week_start": "Sunday starting the week.", "week_end": "Saturday ending the week.", "marketplace_id": "Amazon marketplace id.", "asin": "Our ASIN.", **_AMZ_PRODUCT,
          "orders": "Orders of the ASIN in the week.", "unique_customers": "Unique customers.", "repeat_customers_pct_total": "Percent 0-100 of customers that are repeat customers.",
          "repeat_purchase_revenue": "Revenue from repeat purchases, dollars.", "repeat_purchase_revenue_pct_total": "Percent 0-100 of revenue from repeat purchases.", **_AMZ_LOADED})

rel("public.amazon_settlement_fees_daily", group="amazon", kind="table",
    purpose="Amazon settlement lines summed by posted day and fee or transaction type: what Amazon actually charged and paid (referral and FBA fees, promotions, advertising charges, reimbursements).",
    grain="one row per (day, marketplace_name, transaction_type, amount_type, amount_description)", source=_AMZ_SRC.format(feed="amazon_settlement_fees_daily", tables="amazon_settlement_reports"),
    refresh=_AMZ_REFRESH.format(w="45 days"), coverage="2024-03-18 (a few older rows from 2017) to the newest settlement; settlements arrive in batches, so the newest day can be two weeks old",
    caveats=["Amounts are signed as Amazon stores them: sales are positive, fees, promotions and ad charges negative, refunds negative.",
             "Posted-day basis, so it does not tie to the purchase-day revenue of amazon_orders_asin_daily. 'Cost of Advertising' here is the settlement debit, not the Ads report spend."],
    cols={"day": "Posted day of the settlement line.", "marketplace_name": "Marketplace of the line ('' when blank).", "transaction_type": "Order | Refund | ServiceFee | FBAFees | AmazonFees | other-transaction ...",
          "amount_type": "Fee group, e.g. ItemPrice, ItemFees, Promotion, Cost of Advertising.", "amount_description": "Detail, e.g. Principal, Commission, FBAPerUnitFulfillmentFee.",
          "lines": "Settlement lines summed.", "amount": "Sum of amount in the line currency, signed.", "amount_usd": "Sum of amount in US dollars, signed.", "currency": "Currency code of the lines.", **_AMZ_LOADED})

# the analytics views (one per table; migration 251). Columns and text are inherited from the table.
for _t, _v, _dom, _span in [
    ("amazon_asin_products", "amazon_asin_products", "amazon", ("amazon_asin_products", "refreshed_on")),
    ("amazon_ads_campaign_daily", "amazon_ads_campaign_daily", "amazon", ("amazon_ads_campaign_daily", "day")),
    ("amazon_ads_asin_daily", "amazon_ads_asin_daily", "amazon", ("amazon_ads_asin_daily", "day")),
    ("amazon_sqp_weekly", "amazon_search_queries_weekly", "amazon", ("amazon_sqp_weekly", "period_start", "period_end")),
    ("amazon_sqp_monthly", "amazon_search_queries_monthly", "amazon", ("amazon_sqp_monthly", "period_start", "period_end")),
    ("amazon_orders_asin_daily", "amazon_orders_asin_daily", "amazon", ("amazon_orders_asin_daily", "day")),
    ("amazon_traffic_daily", "amazon_traffic_daily", "amazon", ("amazon_traffic_daily", "day")),
    ("amazon_market_baskets", "amazon_market_baskets", "amazon", ("amazon_market_baskets", "week_start", "week_end")),
    ("amazon_repeat_purchases", "amazon_repeat_purchases", "amazon", ("amazon_repeat_purchases", "week_start", "week_end")),
    ("amazon_settlement_fees_daily", "amazon_settlement_fees_daily", "amazon", ("amazon_settlement_fees_daily", "day")),
]:
    _s = REL[f"public.{_t}"]
    _ex = {
        "amazon_asin_products": [("Products and their ASINs", "SELECT product_slug, asin, sku FROM analytics.amazon_asin_products ORDER BY 1, 2;"), ("Row count", "SELECT count(*) AS rows FROM analytics.amazon_asin_products;")],
        "amazon_ads_campaign_daily": [
            ("Spend and sales by ad type, last 30 days", "SELECT ad_type, round(sum(spend)) AS spend, round(sum(sales_reported)) AS sales_reported, round(sum(sales_reported) / NULLIF(sum(spend), 0), 2) AS roas\n"
             "FROM analytics.amazon_ads_campaign_daily\nWHERE day > (SELECT max(last_day) FROM analytics.data_coverage WHERE source = 'amazon_ads_campaign_daily') - 30\nGROUP BY 1 ORDER BY 2 DESC;"),
            ("Spend by product (NULL = campaign name has no ASIN)", "SELECT coalesce(product_slug, '(no product)') AS product, round(sum(spend)) AS spend\nFROM analytics.amazon_ads_campaign_daily GROUP BY 1 ORDER BY 2 DESC;")],
        "amazon_ads_asin_daily": [("Spend and 14-day sales by ASIN (Sponsored Products)", "SELECT asin, product_slug, round(sum(spend)) AS spend, round(sum(sales_14d)) AS sales_14d\nFROM analytics.amazon_ads_asin_daily WHERE ad_type = 'sp' GROUP BY 1, 2 ORDER BY 3 DESC;"),
                                  ("Row count", "SELECT count(*) AS rows FROM analytics.amazon_ads_asin_daily;")],
        "amazon_search_queries_weekly": [
            ("Brand vs non-brand: our side summed over ASINs, market volume as MAX per query-week",
             "WITH q AS (SELECT period_start, query_key, is_branded, max(search_query_volume) AS volume, sum(asin_purchases) AS our_purchases\n"
             "           FROM analytics.amazon_search_queries_weekly WHERE marketplace = 'US' GROUP BY 1, 2, 3)\n"
             "SELECT is_branded, sum(volume) AS market_searches, sum(our_purchases) AS our_purchases FROM q GROUP BY 1;"),
            ("Top non-branded queries by our purchases, latest week", "SELECT query_key, max(search_query_volume) AS volume, sum(asin_purchases) AS purchases\nFROM analytics.amazon_search_queries_weekly\n"
             "WHERE NOT is_branded AND period_start = (SELECT max(period_start) FROM analytics.amazon_search_queries_weekly) GROUP BY 1 ORDER BY 3 DESC LIMIT 20;")],
        "amazon_search_queries_monthly": [("Row count by month", "SELECT period_start, count(*) AS rows FROM analytics.amazon_search_queries_monthly GROUP BY 1 ORDER BY 1;"),
                                          ("Our purchase share of one query over time", "SELECT period_start, asin, asin_purchase_share FROM analytics.amazon_search_queries_monthly WHERE query_key = 'beef liver' ORDER BY 1, 2;")],
        "amazon_orders_asin_daily": [("Revenue and new-customer revenue by product, last 30 days", "SELECT product_slug, sum(units) AS units, round(sum(revenue)) AS revenue, round(sum(nc_revenue)) AS nc_revenue\nFROM analytics.amazon_orders_asin_daily\n"
                                      "WHERE day > (SELECT max(last_day) FROM analytics.data_coverage WHERE source = 'amazon_orders_asin_daily') - 30 GROUP BY 1 ORDER BY 3 DESC;"),
                                     ("Row count", "SELECT count(*) AS rows FROM analytics.amazon_orders_asin_daily;")],
        "amazon_traffic_daily": [("Sessions and buy box share by week", "SELECT date_trunc('week', day)::date AS week, sum(sessions) AS sessions, round(avg(buy_box_pct), 2) AS buy_box_pct\nFROM analytics.amazon_traffic_daily GROUP BY 1 ORDER BY 1 DESC LIMIT 12;"),
                                 ("Row count", "SELECT count(*) AS rows FROM analytics.amazon_traffic_daily;")],
        "amazon_market_baskets": [("Top companions of each ASIN, latest week", "SELECT asin, purchased_with_asin, purchased_with_rank, combination_pct\nFROM analytics.amazon_market_baskets\nWHERE week_start = (SELECT max(week_start) FROM analytics.amazon_market_baskets) ORDER BY 1, 3;"),
                                  ("Row count", "SELECT count(*) AS rows FROM analytics.amazon_market_baskets;")],
        "amazon_repeat_purchases": [("Repeat customer share by ASIN, latest week", "SELECT asin, orders, unique_customers, repeat_customers_pct_total\nFROM analytics.amazon_repeat_purchases\nWHERE week_start = (SELECT max(week_start) FROM analytics.amazon_repeat_purchases) ORDER BY 2 DESC;"),
                                    ("Row count", "SELECT count(*) AS rows FROM analytics.amazon_repeat_purchases;")],
        "amazon_settlement_fees_daily": [("Fees by type, last 30 days", "SELECT amount_type, amount_description, round(sum(amount_usd)) AS usd\nFROM analytics.amazon_settlement_fees_daily\n"
                                          "WHERE day > (SELECT max(last_day) FROM analytics.data_coverage WHERE source = 'amazon_settlement_fees_daily') - 30 AND transaction_type IN ('Order', 'FBAFees', 'AmazonFees', 'ServiceFee')\nGROUP BY 1, 2 ORDER BY 3;"),
                                         ("Row count", "SELECT count(*) AS rows FROM analytics.amazon_settlement_fees_daily;")],
    }[_v]
    _extra = ""
    if _v in ("amazon_search_queries_weekly", "amazon_search_queries_monthly"):
        _extra = "Read the SUM-vs-MAX rule first. "
    rel(f"analytics.{_v}", group="analytics", kind="view", like=f"public.{_t}", domain=_dom, span=_span, base=_t,
        purpose=_extra + _s["purpose"], grain=_s["grain"], source=f"public.{_t} (ExpandFi merchant 1452)", refresh=_s["refresh"], coverage=_s["coverage"], caveats=list(_s["caveats"]), cols={}, examples=_ex)


# ---------------------------------------------------------------------------------------------------------------------------- Amazon Ads keyword layer (migrations 252 tables, 253 views)
# Sponsored Products only, loaded BY HAND from ExpandFi Data Dash pulls: `python -m etl.amazon_dash_load --dir <folder>` (no cron, not the feeds service). docs/AMAZON_DATA.md
_KW_REFRESH = "manual: python -m etl.amazon_dash_load --dir <folder of Data Dash pulls>; idempotent upsert on the natural key; not scheduled"
_KW_SRC = "ExpandFi Data Dash pull of Amazon Ads {what} (Sponsored Products, US profile), loaded by etl/amazon_dash_load.py"
_KW_LOADED = {"loaded_at": "When the row was last written by the loader."}
_KW_REPORT_COLS = {
    "day": "Amazon Ads report day (US Pacific).", "campaign_id": "Campaign id (text; the reports send an integer).", "ad_group_id": "Ad group id (text).",
    "keyword_id": "Keyword id; for auto and product targets the id of the target (the same id as amazon_ads_targets_snapshot.target_id).",
    "match_type": "BROAD | PHRASE | EXACT | TARGETING_EXPRESSION | TARGETING_EXPRESSION_PREDEFINED (auto and product targets).",
    "campaign_name": "Campaign name on the report row.", "ad_group_name": "Ad group name on the report row.",
    "keyword": "Target text as the report names it: the keyword, or loose-match / close-match / substitutes / complements for auto targets, or an asin or category expression.",
    "impressions": "Impressions.", "clicks": "Clicks.", "cost": "Ad spend, US dollars.",
    "purchases_7d": "Purchases credited to the ad within Amazon's 7-day attribution window (the only window these reports carry).",
    "sales_7d": "Sales dollars credited to the ad within the 7-day window.",
}
_KW_PRODUCT = {"product_slug": "vita-bull | beef-liver | boneflex | vita-belle | vital-flow when the ad group advertises one product; NULL when it advertises several (product_resolution 'mixed') or none is mapped ('unmapped').",
               "product_name": "Display name of product_slug; NULL when product_slug is NULL.",
               "product_resolution": "single | mixed | unmapped: how the ad group's product was found (product ads -> ASIN -> amazon_asin_products)."}
_KW_PRODUCT_CAVEAT = ("product_slug is NULL for an ad group that advertises more than one product (product_resolution = 'mixed') and for one with no product ad on file or no mapped ASIN ('unmapped'): "
                      "count those rows as 'no single product', never as a product.")
_KW_ATTR = "Sponsored Products only, Amazon's 7-day attribution only (no 1, 14 or 30-day columns)."

rel("public.amazon_ads_search_term_daily", group="amazon", kind="table",
    purpose="Sponsored Products search-term report: the customer queries your ads served on, by day, campaign, ad group and the keyword or target that matched them, with impressions, clicks, cost and 7-day purchases and sales.",
    grain="one row per (day, campaign_id, ad_group_id, keyword_id, match_type, search_term)", source=_KW_SRC.format(what="search-term report"), refresh=_KW_REFRESH,
    coverage="2026-09-09 to 2026-11-10 as first loaded; Amazon keeps about 64 days of search terms (nothing returns before 2026-09-09) and the window moves forward one day a day, so days not loaded in time are lost",
    caveats=[_KW_ATTR, "Only queries your ads served on appear: no row is not zero. A day's newest rows can restate for a few days, so re-load the trailing week.",
             "Search terms are not the keywords bid on: one search term can match several keywords and campaigns. No ASIN or placement column; the product comes from the ad group.",
             "search_term_key is the normalised term (lower case, whitespace collapsed), the join key to amazon_sqp_weekly.query_key."],
    cols={**_KW_REPORT_COLS, "search_term": "The customer's query as Amazon reports it (an ASIN in lower case for product-target matches).",
          "search_term_key": "Normalised search_term: lower case, trimmed, runs of whitespace collapsed (the same rule as amazon_sqp_weekly.query_key).", **_KW_LOADED})

rel("public.amazon_ads_targeting_daily", group="amazon", kind="table",
    purpose="Sponsored Products targeting report: every keyword and target (including auto and product targets) by day with impressions, clicks, cost and 7-day purchases and sales: the bid side. It cannot say which query drove the spend.",
    grain="one row per (day, campaign_id, ad_group_id, keyword_id, match_type, targeting)", source=_KW_SRC.format(what="targeting report"), refresh=_KW_REFRESH,
    coverage="2026-08-10 to 2026-11-10 as first loaded; Amazon keeps about 95 days of targeting data and the window moves forward one day a day",
    caveats=[_KW_ATTR, "Includes zero-click rows with impressions only, so the older months are mostly impressions without spend.",
             "Targeting cost and search-term cost for the same days differ by about 0.1% (search terms list only the queries served on)."],
    cols={**_KW_REPORT_COLS, "targeting": "The target expression: the keyword text, loose-match / close-match / substitutes / complements, asin=\"...\" or category=\"...\".", **_KW_LOADED})

rel("public.amazon_ads_keywords_snapshot", group="amazon", kind="table",
    purpose="Sponsored Products keywords with their current bid and state, one snapshot per load date: the bid history accumulates as snapshots are loaded on later days.",
    grain="one row per (snapshot_date, keyword_id)", source=_KW_SRC.format(what="keyword list"), refresh=_KW_REFRESH,
    coverage="one snapshot (2026-11-12, 3,677 keywords: 3,227 enabled, 450 paused); archived keywords are not returned by Amazon; no bid history exists before the first snapshot",
    caveats=["snapshot_date is the date in the snapshot folder name; the latest snapshot is the current bid. Ad group level only (the ad group list was not pulled, so there is no ad group default bid)."],
    cols={"snapshot_date": "Date of the snapshot (from the folder name).", "keyword_id": "Keyword id (text); the same id as keyword_id in the report tables.", "ad_group_id": "Ad group id.", "campaign_id": "Campaign id.",
          "keyword_text": "Keyword text.", "match_type": "BROAD | PHRASE | EXACT.", "bid": "Bid in US dollars at the snapshot.", "state": "ENABLED | PAUSED.", **_KW_LOADED})

rel("public.amazon_ads_targets_snapshot", group="amazon", kind="table",
    purpose="Sponsored Products product and auto targets (targeting clauses) with their current bid and state, one snapshot per load date.",
    grain="one row per (snapshot_date, target_id)", source=_KW_SRC.format(what="targeting clause list"), refresh=_KW_REFRESH,
    coverage="one snapshot (2026-11-12, 919 clauses: 696 enabled, 223 paused; 775 manual, 144 auto)",
    caveats=["target_id equals keyword_id in the report tables. An auto target can have no bid of its own (it inherits the ad group default, which was not pulled)."],
    cols={"snapshot_date": "Date of the snapshot.", "target_id": "Target id (text); the keyword_id of the report rows for product and auto targets.", "ad_group_id": "Ad group id.", "campaign_id": "Campaign id.",
          "bid": "Bid in US dollars; NULL when the target inherits the ad group bid.", "state": "ENABLED | PAUSED.", "expression_type": "MANUAL | AUTO.",
          "expression_text": "The expression in plain text, e.g. 'ASIN_SAME_AS b07tyrty26' or 'QUERY_HIGH_REL_MATCHES'.", "expression": "The expression as Amazon sends it (json array of type / value).",
          "resolved_expression": "The resolved expression (json).", **_KW_LOADED})

rel("public.amazon_ads_negative_keywords_snapshot", group="amazon", kind="table",
    purpose="Sponsored Products negative keywords at ad group level, one snapshot per load date.",
    grain="one row per (snapshot_date, keyword_id)", source=_KW_SRC.format(what="negative keyword list"), refresh=_KW_REFRESH,
    coverage="one snapshot (2026-11-13, 29,995 rows: 29,969 enabled, 26 archived; 28,987 negative phrase, 1,008 negative exact)",
    caveats=["Ad group level only: campaign-level negative keywords and negative product targets are not available through any Data Dash dataset, so absence here does not mean a term is not negated."],
    cols={"snapshot_date": "Date of the snapshot.", "keyword_id": "Negative keyword id (text).", "ad_group_id": "Ad group id.", "campaign_id": "Campaign id.", "keyword_text": "The negative keyword text.",
          "match_type": "NEGATIVE_PHRASE | NEGATIVE_EXACT.", "state": "ENABLED | ARCHIVED.", **_KW_LOADED})

rel("public.amazon_ads_campaigns_snapshot", group="amazon", kind="table",
    purpose="Sponsored Products campaigns with budget, bidding strategy and placement bid adjustments, one snapshot per load date.",
    grain="one row per (snapshot_date, campaign_id)", source=_KW_SRC.format(what="campaign list"), refresh=_KW_REFRESH,
    coverage="one snapshot (2026-11-13, 963 campaigns: 746 enabled, 217 paused)",
    caveats=["Placement bid adjustments live here; Amazon has no placement performance report for these pulls, so the effect of a placement bid cannot be measured from this data.",
             "A placement percentage that is NULL means the campaign sets no adjustment for it."],
    cols={"snapshot_date": "Date of the snapshot.", "campaign_id": "Campaign id (text).", "name": "Campaign name.", "state": "ENABLED | PAUSED.", "targeting_type": "MANUAL | AUTO.",
          "start_date": "Campaign start date.", "budget": "Budget in US dollars.", "budget_type": "DAILY.", "portfolio_id": "Portfolio id, if any.",
          "bidding_strategy": "Dynamic bidding strategy: LEGACY_FOR_SALES | AUTO_FOR_SALES | MANUAL.", "placement_top_pct": "Bid adjustment for top of search, percent.",
          "placement_product_page_pct": "Bid adjustment for product pages, percent.", "placement_rest_of_search_pct": "Bid adjustment for the rest of search, percent.",
          "dynamic_bidding": "The whole dynamic bidding block as Amazon sends it (json: strategy, placement bidding, shopper cohort bidding).", **_KW_LOADED})

rel("public.amazon_ads_product_ads", group="amazon", kind="table",
    purpose="Sponsored Products product ads: which ASIN and SKU each campaign and ad group advertises. The map that gives every keyword row its product.",
    grain="one row per ad_id (the latest snapshot that holds it)", source=_KW_SRC.format(what="product ad list"), refresh=_KW_REFRESH,
    coverage="1,063 product ads from the 2026-11-13 snapshot",
    caveats=["Latest map, not history: an ad that disappears from a later snapshot keeps its older snapshot_date. An ad group with ASINs of more than one product resolves to no single product (see analytics.amazon_ads_ad_group_products)."],
    cols={"ad_id": "Product ad id (text).", "ad_group_id": "Ad group id.", "campaign_id": "Campaign id.", "asin": "Advertised ASIN.", "sku": "Advertised SKU.", "state": "ENABLED | PAUSED.",
          "snapshot_date": "Date of the snapshot this row came from.", **_KW_LOADED})

# the analytics views (migration 253): the tables with the resolved product, one helper at ad group grain and the paid-versus-organic search term view
for _t, _v, _span in [("amazon_ads_search_term_daily", "amazon_ads_search_terms_daily", ("amazon_ads_search_term_daily", "day")),
                      ("amazon_ads_targeting_daily", "amazon_ads_targeting_daily", ("amazon_ads_targeting_daily", "day")),
                      ("amazon_ads_keywords_snapshot", "amazon_ads_keywords_snapshot", ("amazon_ads_keywords_snapshot", "snapshot_date")),
                      ("amazon_ads_targets_snapshot", "amazon_ads_targets_snapshot", ("amazon_ads_targets_snapshot", "snapshot_date")),
                      ("amazon_ads_negative_keywords_snapshot", "amazon_ads_negative_keywords_snapshot", ("amazon_ads_negative_keywords_snapshot", "snapshot_date")),
                      ("amazon_ads_campaigns_snapshot", "amazon_ads_campaigns_snapshot", ("amazon_ads_campaigns_snapshot", "snapshot_date")),
                      ("amazon_ads_product_ads", "amazon_ads_product_ads", ("amazon_ads_product_ads", "snapshot_date"))]:
    _s = REL[f"public.{_t}"]
    _extra_cols = {k: v for k, v in _KW_PRODUCT.items()} if _v != "amazon_ads_product_ads" else {k: _KW_PRODUCT[k] for k in ("product_slug", "product_name")}
    if _v == "amazon_ads_product_ads":
        _extra_cols["product_slug"] = "vita-bull | beef-liver | boneflex | vita-belle | vital-flow; NULL when the ASIN is not in amazon_asin_products."
    _noun = {"amazon_ads_search_terms_daily": "search_term", "amazon_ads_targeting_daily": "targeting"}.get(_v)
    _ex = {
        "amazon_ads_search_terms_daily": [("Top search terms by spend with 7-day return on ad spend", "SELECT search_term_key, sum(clicks) AS clicks, round(sum(cost), 2) AS cost, round(sum(sales_7d), 2) AS sales_7d,\n"
                                           "       round(sum(sales_7d) / NULLIF(sum(cost), 0), 2) AS return_on_ad_spend\nFROM analytics.amazon_ads_search_terms_daily GROUP BY 1 ORDER BY 3 DESC LIMIT 25;"),
                                          ("Rows and spend with no single product", "SELECT product_resolution, count(*) AS rows, round(sum(cost)) AS cost FROM analytics.amazon_ads_search_terms_daily GROUP BY 1 ORDER BY 1;")],
        "amazon_ads_targeting_daily": [("Spend by product and match type", "SELECT coalesce(product_slug, '(' || product_resolution || ')') AS product, match_type, round(sum(cost)) AS cost, sum(clicks) AS clicks\n"
                                        "FROM analytics.amazon_ads_targeting_daily GROUP BY 1, 2 ORDER BY 3 DESC;"),
                                       ("Daily spend", "SELECT day, round(sum(cost), 2) AS cost FROM analytics.amazon_ads_targeting_daily GROUP BY 1 ORDER BY 1 DESC LIMIT 14;")],
        "amazon_ads_keywords_snapshot": [("Current bid of the keywords with most spend", "SELECT k.keyword_text, k.match_type, k.bid, k.state, round(sum(t.cost), 2) AS cost\nFROM analytics.amazon_ads_keywords_snapshot k\n"
                                          "JOIN analytics.amazon_ads_targeting_daily t ON t.keyword_id = k.keyword_id\nWHERE k.snapshot_date = (SELECT max(snapshot_date) FROM analytics.amazon_ads_keywords_snapshot)\n"
                                          "GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 25;"), ("Snapshots held", "SELECT snapshot_date, count(*) AS keywords FROM analytics.amazon_ads_keywords_snapshot GROUP BY 1 ORDER BY 1;")],
        "amazon_ads_targets_snapshot": [("Enabled targets by type", "SELECT expression_type, state, count(*) AS targets FROM analytics.amazon_ads_targets_snapshot GROUP BY 1, 2 ORDER BY 1, 2;"),
                                        ("Row count", "SELECT count(*) AS rows FROM analytics.amazon_ads_targets_snapshot;")],
        "amazon_ads_negative_keywords_snapshot": [("Negative keywords by match type and state", "SELECT match_type, state, count(*) AS negatives FROM analytics.amazon_ads_negative_keywords_snapshot GROUP BY 1, 2 ORDER BY 1, 2;"),
                                                  ("Negatives per product", "SELECT coalesce(product_slug, '(' || product_resolution || ')') AS product, count(*) AS negatives FROM analytics.amazon_ads_negative_keywords_snapshot GROUP BY 1 ORDER BY 2 DESC;")],
        "amazon_ads_campaigns_snapshot": [("Campaigns by strategy and state", "SELECT bidding_strategy, state, count(*) AS campaigns FROM analytics.amazon_ads_campaigns_snapshot GROUP BY 1, 2 ORDER BY 1, 2;"),
                                          ("Placement bid adjustments", "SELECT placement_top_pct, placement_product_page_pct, placement_rest_of_search_pct, count(*) AS campaigns FROM analytics.amazon_ads_campaigns_snapshot GROUP BY 1, 2, 3 ORDER BY 4 DESC;")],
        "amazon_ads_product_ads": [("ASINs advertised per product", "SELECT product_slug, count(DISTINCT asin) AS asins, count(*) AS ads FROM analytics.amazon_ads_product_ads GROUP BY 1 ORDER BY 3 DESC;"),
                                   ("Row count", "SELECT count(*) AS rows FROM analytics.amazon_ads_product_ads;")],
    }[_v]
    rel(f"analytics.{_v}", group="analytics", kind="view", like=f"public.{_t}", domain="amazon", span=_span, base=_t,
        purpose=_s["purpose"], grain=_s["grain"], source=f"public.{_t} joined to the product of the ad group (analytics.amazon_ads_ad_group_products)" if _v != "amazon_ads_product_ads" else "public.amazon_ads_product_ads joined to amazon_asin_products on the ASIN",
        refresh=_s["refresh"], coverage=_s["coverage"], caveats=list(_s["caveats"]) + ([_KW_PRODUCT_CAVEAT] if _v != "amazon_ads_product_ads" else []), cols=_extra_cols, examples=_ex)

rel("analytics.amazon_ads_ad_group_products", group="analytics", kind="view", domain="amazon", span=("amazon_ads_product_ads", "snapshot_date"), base="amazon_ads_product_ads",
    purpose="Which product each Sponsored Products ad group advertises, from its product ads and the ASIN map: the product behind every keyword, search term and negative keyword row.",
    grain="one row per (campaign_id, ad_group_id) that has at least one product ad", source="public.amazon_ads_product_ads joined to public.amazon_asin_products on the ASIN",
    refresh=_KW_REFRESH + " (the ASIN map is refreshed nightly by the feeds service)", coverage="962 ad groups from the 2026-11-13 product ad snapshot",
    caveats=["An ad group that advertises ASINs of more than one product is 'mixed' and has no product_slug: it is counted as no single product, not guessed. 31 of 962 ad groups were mixed on the first load.",
             "An ad group in a report with no product ad on file is not in this view; the views that join it read it as 'unmapped'."],
    cols={"campaign_id": "Campaign id (text).", "ad_group_id": "Ad group id (text).", "asins": "Distinct ASINs advertised in the ad group.", "products": "Distinct products among those ASINs (ASINs with no product mapping are not counted).",
          **_KW_PRODUCT},
    examples=[("Ad groups by resolution", "SELECT product_resolution, count(*) AS ad_groups FROM analytics.amazon_ads_ad_group_products GROUP BY 1 ORDER BY 1;"),
              ("Mixed ad groups", "SELECT campaign_id, ad_group_id, asins, products FROM analytics.amazon_ads_ad_group_products WHERE product_resolution = 'mixed' ORDER BY asins DESC;")])

rel("analytics.amazon_ads_search_terms_vs_organic", group="analytics", kind="view", domain="amazon", span=("amazon_ads_search_term_daily", "day"), base="amazon_ads_search_term_daily",
    purpose="Paid search terms (Sponsored Products) next to the organic Search Query Performance numbers of the same term, product and week: what you pay for against how much of that search you win.",
    grain="one row per (week_start, product_slug, search_term_key); product_slug NULL = an ad group with no single product",
    source="public.amazon_ads_search_term_daily + analytics.amazon_ads_ad_group_products + public.amazon_sqp_weekly (US)", refresh=_KW_REFRESH + " (the organic side is refreshed nightly by the feeds service)",
    coverage="paid side from 2026-09-09; organic columns only where Amazon listed the term for the product that week (about the top 100 terms per product per week)",
    caveats=["week_start is a Sunday: Amazon's Search Query Performance week (Sunday to Saturday), not an ISO (Monday) week, so paid days and organic weeks line up exactly. The first and last paid weeks can be partial.",
             "Organic columns are NULL (not zero) when Amazon did not list the term that week: absence only means it was outside the top terms. has_organic_row says which.",
             "Search Query Performance market totals are the MAX over the ASIN rows of a term-week and our counts the SUM; shares are recomputed here, percent 0 to 100. Our impressions, clicks and purchases include sponsored placements, so they overlap the paid columns: do not add paid and organic.",
             "A paid row with no single product reads all products together in the organic columns (organic_scope = 'all products')."],
    cols={"week_start": "Sunday that starts the Search Query Performance week.", **_KW_PRODUCT, "search_term_key": "Normalised search term (lower case, whitespace collapsed).",
          "impressions": "Paid impressions of the week.", "clicks": "Paid clicks.", "cost": "Paid cost, US dollars.", "purchases_7d": "Paid purchases, 7-day attribution.", "sales_7d": "Paid sales, 7-day attribution.",
          "organic_scope": "product (the organic columns read that product's rows) | all products (the paid row has no single product).",
          "search_volume": "Amazon searches for the term that week (market total); NULL when not listed.",
          "our_impression_share": "Our impressions / all impressions for the term, percent 0-100 (includes our sponsored placements).", "our_click_share": "Our clicks / all clicks for the term, percent 0-100.",
          "our_cart_add_share": "Our cart adds / all cart adds, percent 0-100.", "our_purchase_share": "Our purchases / all purchases for the term, percent 0-100.",
          "our_clicks": "Our clicks on the term in Search Query Performance (organic and sponsored).", "our_purchases": "Our purchases of the term in Search Query Performance (search-origin, same day).",
          "has_organic_row": "True when Search Query Performance has a row for this term, product scope and week."},
    examples=[("Top paid terms with our purchase share", "SELECT search_term_key, round(sum(cost), 2) AS cost, round(sum(sales_7d), 2) AS sales_7d, max(our_purchase_share) AS best_purchase_share\n"
                                                      "FROM analytics.amazon_ads_search_terms_vs_organic GROUP BY 1 ORDER BY 2 DESC LIMIT 25;"),
              ("Share of paid cost that has an organic row", "SELECT round(100.0 * sum(cost) FILTER (WHERE has_organic_row) / NULLIF(sum(cost), 0), 1) AS pct_of_cost_with_organic FROM analytics.amazon_ads_search_terms_vs_organic;")])


# ---------------------------------------------------------------------------------------------------------------------------- deferred views (migration 231)
# Views over tables that live on open pull requests (Klaviyo 206, landing pages 207, Shopify orders 210). Migration 231 creates each one only when its source table exists, with the
# source's own COMMENTs (docs/schema/deferred.py is generated from them), so they are described here but absent from a database that does not have the source yet.
import pathlib as _pl

_ns: dict = {}
exec(compile((_pl.Path(__file__).with_name("deferred.py")).read_text(), "deferred.py", "exec"), _ns)
DEFERRED_VIEWS = _ns["DEFERRED_VIEWS"]
DEFERRED: set[str] = set()
# views of other open pull requests that analytics.catalog should already tag with a domain (the view itself comes from its own migration; its COMMENT is the description)
EXTRA_DOMAINS: dict[str, str] = {}


def _split_comment(text: str) -> dict:
    """The generated comment format: '<purpose> Grain: g. Source: s. Refresh: r. Coverage: c. Caveats: x | y'. Missing parts fall back to a pointer to the purpose."""
    out, rest = {}, text
    markers = [("grain", " Grain: "), ("source", " Source: "), ("refresh", " Refresh: "), ("coverage", " Coverage: "), ("caveats", " Caveats: ")]
    pos = []
    for key, m in markers:
        i = rest.find(m, pos[-1][1] if pos else 0)
        if i >= 0:
            pos.append((key, i, i + len(m)))
    out["purpose"] = (rest[: pos[0][1]] if pos else rest).strip()
    for n, (key, i, j) in enumerate(pos):
        end = pos[n + 1][1] if n + 1 < len(pos) else len(rest)
        out[key] = rest[j:end].strip().rstrip(".") if key != "caveats" else rest[j:end].strip()
    return out


for _name, _v in DEFERRED_VIEWS.items():
    _p = _split_comment(_v["comment"])
    DEFERRED.add(f"analytics.{_name}")
    rel(f"analytics.{_name}", group="analytics", kind="view", domain=_v["domain"], span=_v["span"], base=_v["base"], deferred=True,
        purpose=_p["purpose"], grain=_p.get("grain") or "see the purpose", source=_p.get("source") or f"public.{_name} (migration {_v['source_migration']})",
        refresh=_p.get("refresh") or "as the source", coverage=_p.get("coverage") or "as the source",
        caveats=[c.strip() for c in _p["caveats"].split(" | ")] if _p.get("caveats") else [], cols=dict(_v["cols"]),
        examples=[("First rows", f"SELECT * FROM analytics.{_name} LIMIT 20;"), ("Row count", f"SELECT count(*) AS rows FROM analytics.{_name};")])

# =====================================================================================================================================
# The existing analytics views: domain and the table that gives their first / last day (for analytics.catalog)
# =====================================================================================================================================

for _k, _dom, _span, _base in [
    ("meta_ads", "marketing", None, "meta_ads"),
    ("meta_ad_daily", "marketing", ("ad_daily", "day"), "ad_daily"),
    ("meta_adset_daily", "marketing", ("ad_daily", "day"), "ad_daily"),
    ("meta_campaign_daily", "marketing", ("ad_daily", "day"), "ad_daily"),
    ("meta_campaign_daily_history", "marketing", ("proj_feed_meta_day", "day"), "proj_feed_meta_day"),
    ("meta_reach", "marketing", ("proj_feed_meta_reach", "period_start", "period_end"), "proj_feed_meta_reach"),
    ("meta_breakdown_daily", "marketing", None, "meta_breakdown_daily"),            # no day lookup: a large ad-grain table
    ("meta_change_log", "marketing", ("meta_change_log", "event_day"), "meta_change_log"),
    ("meta_adset_state_daily", "marketing", ("meta_adset_state_daily", "day"), "meta_adset_state_daily"),
    ("meta_change_ledger", "marketing", ("meta_adset_state_daily", "day"), "meta_adset_state_daily"),
    ("google_campaign_daily", "marketing", ("google_daily", "day"), "google_daily"),
    ("platform_daily", "marketing", ("ad_daily", "day"), "ad_daily"),
    ("orders_daily", "orders", ("upstack_orders_daily", "day"), "upstack_orders_daily"),
    ("creative_tracker", "creative", None, "tracker_rows"),
    ("data_coverage", "pipeline", None, None),
    ("etl_runs", "pipeline", ("etl_runs", "started_at::date"), "etl_runs"),
]:
    REL[f"analytics.{_k}"].update(domain=_dom, span=_span, base=_base)

# existing text that said 'not exposed in analytics' for things this migration exposes: their comments are re-written by migration 230 (relation level only)
for _k in ("analytics.orders_daily", "public.proj_feed_orders", "public.proj_feed_amazon", "public.proj_feed_nc_product"):
    REL[_k]["recomment"] = "relation"
RECOMMENT = {k: v["recomment"] for k, v in REL.items() if v.get("recomment")}      # key -> 'relation' | 'all': comments re-written in migration 230
SCOPE_230 = BRAIN_VIEWS | set(RECOMMENT)

# =====================================================================================================================================
# METRICS: the canonical glossary (also analytics.metric_glossary and the 'metrics' key of docs/schema.json). Every formula comes from the code named in source / traps.
# Contract (another tool server reads this exact shape): name, label, formula, source, default_basis, other_bases (list), traps (list), domain (one of DOMAINS).
# `source` = analytics.<view>.<column(s)>: columns of one view are joined with commas, views are separated by '; '.
# =====================================================================================================================================

METRIC_FIELDS = ("name", "label", "formula", "source", "default_basis", "other_bases", "traps", "domain")
_TRENDS = "app/trends.py build_catalog()"
_PACING = "app/projection/pacing/catalog.py"
_NOT_UPSTACK_MIX = "Never compare Upstack new customers with Meta's all purchases: Meta counts returning customers, Upstack's new-customer basis does not (they are different quantities, not one number measured twice)."

METRICS = [
    dict(name="spend", label="Ad spend", domain="marketing",
         formula="sum(spend) over the days. Total spend = Meta spend + Google spend; a ratio's spend is always the sum over the same days and rows as its numerator.",
         source="analytics.meta_ad_daily.spend; analytics.google_campaign_daily.spend; analytics.platform_daily.spend",
         default_basis="Meta (Graph API, both ad accounts) + Google Ads, platform-reported dollars: the 'Total spend' of the Marketing metrics app (" + _TRENDS + ", total_spend).",
         other_bases=["Meta spend alone (the product bands' Total column divides by it)",
                      "Business-wide spend in pacing = Meta + Google + TikTok and other platforms + Amazon ads (" + _PACING + ", biz_total_spend)",
                      "analytics.platform_daily_history.spend for days before 2026-05-06 (Meta ad level starts 2026-05-06; campaign grain from 2024-12-31 in analytics.meta_campaign_daily_history)"],
         traps=["Spend adds across platforms; purchases and revenue never do.",
                "Meta days are each ad account's own day (Main = Los Angeles, BackUp = New York); Google, Upstack and orders are Pacific days.",
                "The last 3-7 days still move (Meta restates), and today is never loaded."]),
    dict(name="meta_purchases", label="Meta-reported purchases", domain="marketing",
         formula="sum(meta_purchases): purchases Meta reports for the ad that day (omni_purchase, else purchase, else pixel purchase), Meta's default attribution.",
         source="analytics.meta_ad_daily.meta_purchases; analytics.meta_campaign_daily.meta_purchases",
         default_basis="Meta-reported (counts returning customers, Meta's attribution window)",
         other_bases=["Upstack new customers (analytics.meta_ad_daily.upstack_new_customers): first orders only", "Upstack all orders (upstack_orders): every order Upstack credits"],
         traps=[_NOT_UPSTACK_MIX, "Meta and Google credit the same orders: never add Meta purchases to Google's.", "Meta counts a returning customer's order as a purchase; it is not a new customer."]),
    dict(name="landing_page_views", label="Meta landing page views", domain="marketing",
         formula="sum(landing_page_views): landing page views Meta reports for the ad that day (omni_landing_page_view, else landing_page_view): clicks where the page actually loaded.",
         source="analytics.meta_ad_daily.landing_page_views",
         default_basis="Meta-reported (default attribution), all ad accounts",
         other_bases=["Link clicks (analytics.meta_ad_daily.link_clicks): clicks before the page loads, always at least as large", "Unique outbound clicks (analytics.meta_reach / meta_product_reach): de-duplicated per person, a different denominator"],
         traps=["NULL (not 0) on days before the 2026-11-12 funnel backfill: sum only over rows where it is not NULL, and divide by the matching rows' other columns.",
                "LPV / link clicks is typically 70-95%: a lower share means slow pages or lost clicks, not fewer buyers.",
                "Not comparable to Upstack site visitors (analytics.site_traffic_daily): different tracker."]),
    dict(name="add_to_carts", label="Meta adds to cart", domain="marketing",
         formula="sum(add_to_carts): adds to cart Meta reports for the ad that day (omni_add_to_cart, else add_to_cart, else pixel add to cart), the 'Adds to cart' of Ads Manager.",
         source="analytics.meta_ad_daily.add_to_carts",
         default_basis="Meta-reported (default attribution, includes returning customers)",
         other_bases=["Meta purchases (analytics.meta_ad_daily.meta_purchases)"],
         traps=["NULL (not 0) on days before the 2026-11-12 funnel backfill.",
                "The funnel rates are ratios of sums over the same ad-days and rows: link click to add to cart = sum(add_to_carts) / sum(link_clicks); landing page view to add to cart = sum(add_to_carts) / sum(landing_page_views); add to cart to purchase = sum(meta_purchases) / sum(add_to_carts). Never average per-ad rates.",
                "Purchases count returning customers, so add to cart to purchase is a Meta-attribution rate, not a new-customer conversion rate."]),
    dict(name="initiated_checkouts", label="Meta initiated checkouts", domain="marketing",
         formula="sum(initiated_checkouts): checkouts Meta reports initiated for the ad that day (omni_initiated_checkout, else initiate_checkout, else pixel initiate checkout), the 'Checkouts initiated' of Ads Manager.",
         source="analytics.meta_ad_daily.initiated_checkouts",
         default_basis="Meta-reported (default attribution, includes returning customers)",
         other_bases=["Meta purchases (analytics.meta_ad_daily.meta_purchases)", "Upstack orders (analytics.meta_ad_daily.upstack_orders): another tracker"],
         traps=["NULL (not 0) on days before the checkout backfill: sum only over rows where it is not NULL, and divide only by sums over the same rows.",
                "Funnel rates are ratios of sums over the same ad-days: add to cart to initiated checkout = sum(initiated_checkouts) / sum(add_to_carts); initiated checkout to purchase = sum(meta_purchases) / sum(initiated_checkouts); payment info to purchase = sum(meta_purchases) / sum(payment_info_adds). Never average per-ad rates.",
                "Purchases count returning customers, so initiated checkout to purchase is a Meta-attribution rate, not a new-customer conversion rate. Checkout events fire on the pixel: a checkout that never loads the pixel (an in-app browser that blocks it) is not counted, so a fall in initiated checkouts is not proof of fewer checkouts."]),
    dict(name="payment_info_adds", label="Meta payment info added", domain="marketing",
         formula="sum(payment_info_adds): payment-info-added events Meta reports for the ad that day (omni_add_payment_info, else add_payment_info, else pixel add payment info), the step between initiated checkout and purchase.",
         source="analytics.meta_ad_daily.payment_info_adds",
         default_basis="Meta-reported (default attribution, includes returning customers)",
         other_bases=["Initiated checkouts (analytics.meta_ad_daily.initiated_checkouts)", "Meta purchases (analytics.meta_ad_daily.meta_purchases)"],
         traps=["NULL (not 0) on days before the checkout backfill.",
                "It can exceed initiated checkouts on a day (a customer re-entering payment details, or express wallets that skip the initiate event): use it as a trend, not as a stage that must be smaller than the one before.",
                "Same attribution as purchases, so it counts returning customers."]),
    dict(name="atc_to_ic_rate", label="Add to cart to initiated checkout rate", domain="marketing",
         formula="sum(initiated_checkouts) / sum(add_to_carts): the share of adds to cart that go on to start checkout, as a ratio of sums over the same ad-days (Meta-reported).",
         source="analytics.meta_ad_daily.initiated_checkouts,add_to_carts",
         default_basis="Meta-reported (default attribution, includes returning customers)",
         other_bases=["Link click to add to cart and landing page view to add to cart (see add_to_carts)"],
         traps=["Restrict to rows where both columns are not NULL; never average per-ad rates.", "Not a new-customer rate: Meta counts returning customers in both counts."]),
    dict(name="ic_to_purchase_rate", label="Initiated checkout to purchase rate", domain="marketing",
         formula="sum(meta_purchases) / sum(initiated_checkouts): the share of started checkouts that Meta credits with a purchase, as a ratio of sums over the same ad-days (Meta-reported).",
         source="analytics.meta_ad_daily.meta_purchases,initiated_checkouts",
         default_basis="Meta-reported (default attribution, includes returning customers)",
         other_bases=["Upstack new-customer orders per initiated checkout (upstack_new_customers / initiated_checkouts) mixes bases: say so if used", "Add to cart to purchase (see add_to_carts)"],
         traps=["Restrict to rows where initiated_checkouts is not NULL; never average per-ad rates.", "Purchases use Meta's attribution window and can land on a later day than the checkout, so a single day's rate is noisy and the last days still move."]),
    dict(name="meta_change_log", label="Meta change log (budget, bid, status, targeting changes)", domain="marketing",
         formula="count of rows in analytics.meta_change_log by event_type and event_day; budget and bid amounts in old_amount / new_amount (dollars). Ad set settings by day: analytics.meta_adset_state_daily; their day-over-day differences: analytics.meta_change_ledger.",
         source="analytics.meta_change_log.event_type,old_amount,new_amount",
         default_basis="Meta activity log (read only), both ad accounts",
         other_bases=["Ad set snapshot differences (analytics.meta_change_ledger), from its first snapshot day: independent of the log's extra_data"],
         traps=["A change is not a cause: say 'a bid change on N ad sets preceded the move', never 'the bid change caused it', unless a control supports it.",
                "The log's extra_data is often incomplete for bids and caps; cross-check with analytics.meta_change_ledger, which only exists from its first snapshot day.",
                "System events (first_delivery_event, Pending Review flips, billing, image adds) dominate the row count: filter to the event types you mean.",
                "Campaign budget (CBO) changes are update_campaign_budget with object_level campaign; ad set budgets are update_ad_set_budget with object_level adset."]),
    dict(name="meta_roas", label="Meta ROAS (platform ROAS)", domain="marketing",
         formula="sum(meta_revenue) / sum(spend): Meta-reported purchase value over spend, as a ratio of sums.",
         source="analytics.meta_ad_daily.meta_revenue,spend; analytics.meta_campaign_daily.meta_revenue,spend",
         default_basis="Meta-reported purchase value / Meta spend",
         other_bases=["Upstack new-customer ROAS (upstack_nc_roas)", "Upstack all-orders ROAS (upstack_all_orders_roas)", "Total NC ROAS (total_nc_roas): actual new-customer revenue over total spend"],
         traps=["Never average daily ROAS: divide the sums.", "Meta ROAS counts returning customers and runs well above Total NC ROAS: they answer different questions.",
                "Creative-level ROAS in analytics.creative_family_metrics / priorities_concepts is the same Meta-reported ratio but over each ad's life since launch (blank under $100 spend in the priorities snapshot)."]),
    dict(name="meta_cpa", label="Meta cost per purchase", domain="marketing",
         formula="sum(spend) / sum(meta_purchases).", source="analytics.meta_ad_daily.spend,meta_purchases",
         default_basis="Meta-reported purchases", other_bases=["Upstack new-customer CPA over Meta spend (spend / upstack_new_customers)", "Total NC CPA (nc_cpa): total spend / actual new customers"],
         traps=[_NOT_UPSTACK_MIX, "This is not the NC CPA the business quotes (that one divides by actual new customers, see nc_cpa)."]),
    dict(name="upstack_new_customers", label="Upstack-attributed new customers", domain="attribution",
         formula="sum(upstack_new_customers): first-time customers Upstack credits to the ad under any-click, 30-day attribution (utm_source 'Meta Ads').",
         source="analytics.meta_ad_daily.upstack_new_customers; analytics.google_campaign_daily.upstack_new_customers",
         default_basis="Upstack new customers (first orders only, any-click 30-day, Pacific days)",
         other_bases=["Upstack all orders (upstack_orders): every order credited, returning included", "Last-click by source (analytics.attribution_source_daily): exclusive per order, a different basis"],
         traps=["NULL, not 0, on days Upstack was not pulled (Meta before 2026-05-06, Google before 2025-05-13).",
                "Any-click credits an ad on days it spent nothing (spend = 0, upstack_* > 0 is normal); one customer can be credited to several ads, so sums over ads of different families can exceed orders_daily.new_customers.",
                "About 1.3% of credited orders match no Meta ad (analytics.attribution_unmapped_daily): in orders_daily, in no ad row.", _NOT_UPSTACK_MIX]),
    dict(name="upstack_nc_roas", label="Upstack new-customer ROAS (attributed)", domain="attribution",
         formula="sum(upstack_new_customer_revenue) / sum(spend) over the rows where Upstack was pulled (upstack_new_customers IS NOT NULL).",
         source="analytics.meta_ad_daily.upstack_new_customer_revenue,spend; analytics.google_campaign_daily.upstack_new_customer_revenue,spend",
         default_basis="Upstack new customers (the default basis everywhere in the Marketing metrics app: 'Count: New customers')",
         other_bases=["Upstack all orders (upstack_all_orders_roas)", "Meta-reported (meta_roas)"],
         traps=[_NOT_UPSTACK_MIX, "Restrict spend to days Upstack covers or the ratio is diluted by uncovered days.",
                "When one ad is copied into several ad sets under one name, Upstack's credit is split by Meta spend that day: family and higher sums are exact, one copy's own numbers are an allocation.",
                "Attribution, not the actual: the business's real new-customer revenue is orders_daily (see nc_revenue / total_nc_roas)."]),
    dict(name="upstack_all_orders_roas", label="Upstack all-orders ROAS (attributed)", domain="attribution",
         formula="sum(upstack_order_revenue) / sum(spend) over the rows where Upstack was pulled.",
         source="analytics.meta_ad_daily.upstack_order_revenue,spend; analytics.google_campaign_daily.upstack_order_revenue,spend",
         default_basis="Upstack all orders (every order Upstack credits to the ad, any-click 30 days)",
         other_bases=["Upstack new customers (upstack_nc_roas): the default basis", "Meta-reported (meta_roas)"],
         traps=["Use this only against Meta's all-purchases basis, never against upstack_nc_roas.", "Includes returning customers' orders, so it flatters acquisition efficiency; the Marketing metrics app shows it only when 'Count: All orders' is chosen."]),
    dict(name="nc_revenue", label="New-customer revenue (actual)", domain="orders",
         formula="sum(new_customer_revenue): first-order revenue per Pacific day, counted once (Upstack order-level measures).",
         source="analytics.orders_daily.new_customer_revenue; analytics.orders_by_product_daily.new_customer_revenue",
         default_basis="Actual orders feed (analytics.orders_daily), gross: tax included, before refunds",
         other_bases=["By product (analytics.orders_by_product_daily): order total split across product lines by net line price", "Attributed to Meta or Google (upstack_new_customer_revenue): an attribution, not the actual"],
         traps=["Not the sum of platform columns: platforms overlap, this is counted once.", "A day with no orders is absent from orders_daily, not 0.",
                "Revenue is gross of tax and refunds (the Marketing metrics app describes it as 'tax included, before refunds'); net = total_revenue - tax - refunds in analytics.orders_daily_detail.",
                "The trailing 7 days are restated nightly."]),
    dict(name="nc_orders", label="New customers (actual)", domain="orders",
         formula="sum(new_customers): customers whose first Shopify order fell on the day, counted once.",
         source="analytics.orders_daily.new_customers; analytics.orders_by_product_daily.new_customers",
         default_basis="Actual orders feed (analytics.orders_daily)", other_bases=["By product (orders whose largest product line is the product)", "Upstack-attributed new customers (upstack_new_customers)"],
         traps=["Not additive with attributed new customers: those credit the same customer to several ads or platforms.", "Customers, not orders: a first order is the unit."]),
    dict(name="total_nc_roas", label="Total NC ROAS", domain="orders",
         formula="sum(orders_daily.new_customer_revenue) / (sum(Meta spend) + sum(Google spend)) over the days that Meta, Google and orders_daily all cover, as a ratio of sums.",
         source="analytics.orders_daily.new_customer_revenue; analytics.platform_daily.spend",
         default_basis="Actual new-customer revenue / Meta + Google spend (" + _TRENDS + ", nc_roas)",
         other_bases=["NC ROAS on Meta spend alone (meta_nc_roas in " + _TRENDS + "): the product bands' Total column",
                      "Business-wide NC ROAS in pacing = (Shopify + Amazon new-customer revenue) / (Meta + Google + TikTok + other + Amazon ads) (" + _PACING + ", biz_nc_roas)",
                      "Per-product NC ROAS = analytics.orders_by_product_daily revenue / analytics.meta_product_daily spend"],
         traps=["The numerator is actual revenue counted once, not the sum of the platforms' purchase values.", "Use only days all three sources cover and compute the ratio of sums, never an average of daily ratios.",
                "Gross revenue (tax included, before refunds); the deal's net-of-tax goal basis is lower.", "Amazon is not in this ratio (the Marketing metrics app's total covers Shopify, Meta and Google)."]),
    dict(name="nc_cpa", label="NC CPA (cost per new customer)", domain="orders",
         formula="(sum(Meta spend) + sum(Google spend)) / sum(orders_daily.new_customers), over the days all three sources cover.",
         source="analytics.platform_daily.spend; analytics.orders_daily.new_customers",
         default_basis="Total spend / actual new customers (" + _TRENDS + ", nc_cpa)",
         other_bases=["Meta spend / actual new customers (meta_nc_cpa): the product bands' Total column", "Testing / scaling NC CPA = stage spend / any-click new customers of that stage (analytics.attribution_anyclick_daily)",
                      "Upstack-attributed NC CPA = spend / upstack_new_customers (a different, attributed basis)"],
         traps=["Divides by ACTUAL new customers, not by attributed ones or by Meta purchases.", "Per-product NC CPA counts new customers whose largest product line is the product (analytics.orders_by_product_daily)."]),
    dict(name="mer", label="MER (marketing efficiency ratio)", domain="orders",
         formula="sum(total_revenue) / (sum(Meta spend) + sum(Google spend)) over the days all three sources cover.",
         source="analytics.orders_daily.total_revenue; analytics.platform_daily.spend",
         default_basis="Orders feed total revenue (no Amazon) / Meta + Google spend (" + _TRENDS + ", mer)",
         other_bases=["Business-wide MER in pacing adds Amazon revenue and Amazon / TikTok / other spend (" + _PACING + ", biz_mer)"],
         traps=["Total revenue here is gross (tax included, before refunds) and has no Amazon revenue; the matching spend has no Amazon or TikTok spend."]),
    dict(name="conversion_rate_uoc", label="Conversion rate on unique outbound clicks", domain="marketing",
         formula="actual new customers / Meta unique outbound clicks: per product = orders_by_product_daily.new_customers / meta_product_reach.unique_outbound_clicks; total = orders_daily.new_customers / meta_reach.unique_outbound_clicks.",
         source="analytics.orders_by_product_daily.new_customers; analytics.meta_product_reach.unique_outbound_clicks; analytics.orders_daily.new_customers; analytics.meta_reach.unique_outbound_clicks",
         default_basis="Actual new customers / unique outbound clicks (" + _PACING + ", meta_cvr_<product> and prod_cvr_total; every click-based pacing metric uses UNIQUE OUTBOUND clicks)",
         other_bases=["Meta CVR = Meta purchases / Meta link clicks (meta_cvr)", "NC conversion (clicks) = new customers / (Meta link clicks + Google clicks) (" + _TRENDS + ", nc_cvr_clicks)",
                      "NC conversion (visitors) = new customers / Upstack new visitors (analytics.site_traffic_daily): the Marketing metrics app's nc_cvr"],
         traps=["Unique outbound clicks exist only from 2026-07-05 (Meta serves filtered unique clicks ~120 days back); earlier periods are blank, not 0.",
                "Unique clicks are not additive: read the row of the period's own grain (day, week or month) in meta_reach / meta_product_reach.",
                "The three products' unique clicks add to 1.01-1.06x the account-level figure, so the product rates do not average to the total.",
                "Meta's total link_click count (analytics.meta_ad_daily.link_clicks) is a different, larger denominator than unique outbound clicks: do not mix the two."]),
    dict(name="cpm", label="CPM (cost per 1,000 impressions)", domain="marketing",
         formula="sum(spend) / sum(impressions) x 1000.", source="analytics.meta_ad_daily.spend,impressions; analytics.meta_reach.impressions",
         default_basis="Meta spend / Meta impressions x 1000 (" + _TRENDS + ", m_cpm)",
         other_bases=["Pacing divides spend by the account-level impressions pulled at the period's own grain (" + _PACING + ", meta_cpm)", "Cost per 1,000 reached = spend / reach x 1000 (a different, higher number)"],
         traps=["Impressions add across days, reach does not: for CPM either source works, for frequency only meta_reach.", "Meta days are the ad account's own day."]),
    dict(name="frequency", label="Frequency", domain="marketing",
         formula="impressions / reach, both taken at the SAME grain (a day's, a week's or a month's own row): analytics.meta_reach.frequency.",
         source="analytics.meta_reach.impressions,reach,frequency",
         default_basis="Account level, both ad accounts, at the grain you read (" + _PACING + ", meta_frequency)",
         other_bases=["No frequency exists per ad, ad set, campaign or product (reach is account level only)"],
         traps=["Reach is de-duplicated by Meta and NOT additive: never average daily frequencies or sum daily reach to get a week or month; use the week / month rows.",
                "The current day, week and month are partial.", "Frequency here is the same-day overlap between ad sets, not exposure on more days."]),
    dict(name="hit_rate", label="Creative hit rate", domain="creative",
         formula="Display hit rate = ads whose since-launch spend reached $10,000 / launched ads (first tracker rows of an ad family with a launch date or any spend). Scoring hit rate = mature (>= 14 days old) judged ads (>= $100 spent) that reached $1,000 / mature judged ads, shrunk toward the product average.",
         source="analytics.creative_family_metrics.spend_launch,is_first_row,launch_date; analytics.priorities_concepts.ads_10k_plus_spend,ads_launched,hit_rate",
         default_basis="Display: $10,000 since-launch spend over launched ads, Meta basis (etl/creative_priorities.py TOP_SPEND = 10000; the definition of a hit since 2026-11-10, docs/CREATIVE_PRIORITIES.md)",
         other_bases=["Scoring: HIT_SPEND = $1,000 over mature judged ads (priorities_concepts.hit_rate, hit_rate_index): the stored hit_rate in the snapshot and in the CSV export's hit_rate / hit_rate_index columns",
                      "Hits out of tested ads: $10,000 / ads that spent $100 or more (hit_rate_10k_of_tested)"],
         traps=["A KNOWN SPLIT: the displayed hit rate uses $10,000, but the priorities CSV export's hit_rate and hit_rate_index columns (and the score's hit-rate component) still use $1,000 (etl/creative_priorities.py: HIT_SPEND vs TOP_SPEND; docs/CREATIVE_PRIORITIES.md). Name which one you quote.",
                "Launched counts every launched ad including recent ones with $0 spend; new ads need time to reach $10,000, so young cohorts read low.",
                "Metrics sit on the FIRST tracker row of each ad family: filter is_first_row."]),
    dict(name="test_to_scale_rate", label="Test-to-scale rate (% reached scaling)", domain="creative",
         formula="creatives that moved / creatives that began in testing, among creatives launched in the window and not launched before the data starts: count(*) FILTER (WHERE moved) / count(*) WHERE began_in_testing AND NOT launched_before_data_start AND first_day in window.",
         source="analytics.creative_stage_moves.moved,began_in_testing,launched_before_data_start,first_day,days_in_test",
         default_basis="Meta basis (analytics.creative_stage_moves.basis = 'meta'); the basis changes only the money columns, never who moved (app/promotions.py _cohorts / build)",
         other_bases=["upstack_new_customers and upstack_all_orders: same creatives and moves, Upstack purchases and revenue"],
         traps=["A creative MOVED only when it spent in a Testing campaign and first spent in a Scaling campaign on a LATER day (a campaign is Testing when its name contains 'test').",
                "Hide bulk days: 40 or more creatives entering scaling on one day (moves_that_day >= 40) is a mass relaunch, not a set of test winners.",
                "Creatives that launched straight into scaling are not testers and never in the rate; a new creative needs time to move (analytics.creative_stage_moves.days_in_test): recent cohorts read low.",
                "'Held up' = (scale_revenue / scale_spend) / (test_revenue / test_spend): held >= 0.90, softer >= 0.70, fell below; too early before $200 and 3 days in scaling."]),
    dict(name="repeat_revenue", label="Repeat revenue", domain="cohorts",
         formula="Modeled: sum over cohorts of first-order revenue x curve[age] in the month at that age (age 0 = the acquisition month has no repeat): max(0, new_customers x first_order_per_customer x curve[age]) (app/projection/cohorts/engine.py repeat_for).",
         source="analytics.cohorts_monthly.first_order_revenue; analytics.cohorts_curve_cumulative.increment_ratio; analytics.cohorts_curve_goal.value",
         default_basis="Modeled from the cohort table and the chosen retention curve (Live or Goal; scenario lever cohorts.curve_source in analytics.projection_levers)",
         other_bases=["Actual returning revenue = analytics.orders_daily.returning_customer_revenue (Upstack, gross)", "Business-wide repeat = Shopify returning revenue + Amazon revenue not from new customers (" + _PACING + ", biz_repeat_rev)",
                      "Historical months come from the books (analytics.projection_actuals 'pnl.repeat_rev'); the model only projects forward"],
         traps=["Repeat revenue is a projection driven by the curve choice; always say which curve (Live last N cohorts, Goal snapshot, as-of month).",
                "The cohort table must hold only CLOSED months: a cell that lands in a month that is not over is blanked.", "Gross of tax and refunds, like all pacing revenue."]),
    dict(name="retention_curve", label="Retention curve (Live vs Goal)", domain="cohorts",
         formula="Repeat dollars per $1 of first-order revenue by age (months after acquisition). Live at age a = weighted average (weights new customers) of increment / first_order over the newest N cohorts that have a non-blank, non-zero increment at that age (usable_rank <= N); from age 28 on = previous age x 0.95. Goal = the newest saved snapshot of analytics.cohorts_curve_goal.",
         source="analytics.cohorts_curve_cumulative.increment_ratio,new_customers,usable_rank; analytics.cohorts_curve_goal.value,age,is_active",
         default_basis="Live curve over the last 3 cohorts (app/projection/cohorts/engine.py Levers.n_cohorts = 3, tail_decay 0.95, tail_from_age 28)",
         other_bases=["Live over the last 6 cohorts (the P&L page offers 3 or 6; the deal notes mention 6; scenario lever cohorts.n_cohorts)", "Goal curve (analytics.cohorts_curve_goal, is_active)",
                      "As-of curve: the Live curve re-derived from what was observable at the end of a past month", "Blend: w x Goal + (1 - w) x Live"],
         traps=["Say Live or Goal and N: the lifetime sums differ (compare sum(value) of the Goal curve with the Live curve you compute).", "Never include a month that is not over; cohort exports differ by source (Shopify cohort dollars vs Upstack's), so do not mix sources between refreshes.",
                "Ages beyond 36 are the tail formula, not data."]),
    dict(name="payback", label="Payback (months)", domain="cohorts",
         formula="First age (months after the first order, 0 = the first order pays) where cumulative contribution per new customer >= CAC. Inputs (aov, cac, product_cost_pct, fulfillment, max_age, curve) = analytics.projection_levers where module = 'financing' and lever = 'payback' (a json object holding only what the scenario sets; unset keys take the code defaults in app/projection/financing/params.py: product_cost_pct 0.1726, fulfillment 5.53, max_age 36, curve 'live'; aov and cac default to None = not typed). Contribution of the first order cm = AOV x (1 - product cost %) - fulfillment per order; month k adds cm x curve[k]; cumulative = cm + sum cm x curve.",
         source="analytics.projection_levers.module,lever,value; analytics.cohorts_curve_cumulative.increment_ratio; analytics.cohorts_monthly.cac,payback_months",
         default_basis="Product margin: cost per order = product cost % x AOV + fulfillment (app/projection/financing/calc.py payback(); the quick hint app/projection/drivers/payback.py is the same arithmetic)",
         other_bases=["Fully loaded ('all variable costs'): cost per order = loaded_rate x AOV with the P&L's own product, fulfillment, shipping, payment and chargeback rates (fully_loaded=True)",
                      "Interpolated months (months_interpolated) between the last unpaid and the first paid age", "cohorts_monthly.payback_months: the payback exported with each cohort (a different, historical measure)"],
         traps=["Payback on product margin is much shorter than fully loaded: always name the basis.", "The curve behind it (Live N cohorts vs Goal) moves the answer by a month at some scales.",
                "Contribution, not revenue: do not compare cumulative revenue with CAC."]),
    dict(name="gross_vs_net_revenue", label="Gross vs net revenue (pacing is gross)", domain="orders",
         formula="Gross = total_revenue (tax included, before refunds). Net = total_revenue - tax - refunds. Plan basis = total_revenue - the plan's sales-tax estimate (+ Amazon).",
         source="analytics.orders_daily.total_revenue; analytics.orders_daily_detail.total_revenue,tax,refunds",
         default_basis="Gross: the pacing page, the P&L and every ROAS / MER here are gross; refunds are a memo row only (decision 2026-11-04)",
         other_bases=["Net of tax and refunds (" + _PACING + ", biz_total_rev_net)", "Plan basis: Shopify total less the sales-tax estimate, plus Amazon: the basis of the P&L revenue goal (biz_total_rev_plan)"],
         traps=["The monthly plan block and Month Snapshot compare against the P&L goal, which is net of the estimated sales tax (docs/projection/MODULE_pacing.md): comparing gross Shopify revenue with that goal overstates pacing.", "Refunds are not deducted anywhere unless you subtract them (net = gross - tax - refunds): refunds were 11% of DTC revenue in Nov 1-2, so a net-of-refunds basis paced about 101% of goal, not 106% (docs/projection/MODULE_pacing.md, open items)."]),
    dict(name="sales_tax_deduction", label="Sales-tax deduction (estimate)", domain="projection",
         formula="Sales tax(m) = 0 for a month whose DTC revenue is loaded from the books (already net of tax), else -(sales_tax_rate x DTC revenue(m)); sales_tax_rate = analytics.projection_levers where module = 'drivers' and lever = 'sales_tax_rate'; no row means the code default 0.07. It reduces total revenue.",
         source="analytics.projection_levers.module,lever,value; analytics.orders_daily_detail.tax",
         default_basis="Estimate: 7% of DTC revenue for months not yet closed in the books (app/projection/drivers/revenue.py, drivers/model.py DEFAULT_SALES_TAX)",
         other_bases=["Actual tax collected (analytics.orders_daily_detail.tax), used by pacing's net view", "Closed books months carry no deduction"],
         traps=["Open question (docs/projection/SKEPTIC_REPORT.md, SHEET_FINDINGS.md S1): the deduction applies from Jul-26 while history months carry none and the DTC revenue behind it is already net of tax; removing it adds about $535K to FY27 NOI. Settle against the Aug QuickBooks close or set drivers.sales_tax_rate to 0.",
                "DTC rows of the pacing page stay gross on purpose; only the goal / projection total is net of the estimate."]),
    dict(name="repeat_share", label="Repeat share of revenue", domain="orders",
         formula="sum(returning_customer_revenue) / sum(total_revenue).", source="analytics.orders_daily.returning_customer_revenue,total_revenue",
         default_basis="Actual orders feed, Shopify only (" + _TRENDS + ", repeat_share)", other_bases=["Business-wide: (Shopify returning + Amazon non-new-customer revenue) / (Shopify + Amazon revenue) (" + _PACING + ", biz_repeat_share)"],
         traps=["Gross of tax and refunds; no Amazon in the Shopify-only ratio."]),
    dict(name="daily_story_baseline", label="Same-weekday baseline (daily story)", domain="story",
         formula="For a target day D: the same weekday in the previous four weeks (D-7, D-14, D-21, D-28; fewer when history is short). Tables show the median of those days' own values and the pooled value (sum / sum); the decomposition uses the pooled value.",
         source="analytics.daily_story_facts.facts",
         default_basis="median of the up to four same-weekday days for the metric table; pooled for the exact decomposition",
         other_bases=["trailing 7 days before D, pooled (metrics.<key>.trailing_7d)"],
         traps=["A same-weekday baseline removes the weekday pattern only; it does not hold changes (new ad sets, bids, sends) constant, so it is not a like-for-like control for any one cause.", "Early days (May 2026) have fewer than four baseline weeks."]),
    dict(name="daily_story_noise_z", label="Noise range and z-score (daily story)", domain="story",
         formula="noise = sample standard deviation of the previous 28 days of the same daily metric; z = (value on D - same-weekday median baseline) / noise; a metric is flagged when |z| >= 2. Needs at least 10 trailing days.",
         source="analytics.daily_story_facts.facts",
         default_basis="daily values of the metric on the same basis as the value (ratios from each day's sums)",
         other_bases=["Facts path: metrics.<key>.z, noise_std_28d and flag inside the JSON"],
         traps=["A daily ratio's noise is large (NC ROAS moves several tenths day to day), so most days are inside it.", "z compares one day with a baseline; it does not say why the day moved."]),
    dict(name="daily_story_decomposition", label="Exact decomposition of NC ROAS (daily story)", domain="story",
         formula="NC ROAS = AOV x (Meta spend / total spend) x (1000 / CPM) x CTR x (link click to landing page view) x (view to add to cart) x (add to cart to new customer) [x add to cart to initiated checkout, initiated checkout to new customer when the column exists]. The log change against the pooled same-weekday baseline splits exactly into one contribution per term; percentage points are the total percentage move split in proportion to each term's log.",
         source="analytics.daily_story_facts.facts",
         default_basis="headline NC ROAS: orders_daily new-customer revenue / (Meta + Google spend); the funnel terms use Meta link clicks, landing page views and adds to cart over all actual new customers",
         other_bases=["ad-attributed NC ROAS (a cut for ads, products, structures): sum(upstack_new_customer_revenue) / Meta spend"],
         traps=["Arithmetic, not cause: it says which term moved, not why.", "add to cart to new customer divides all actual new customers (including non-Meta ones) by Meta adds to cart, so it is a ratio of the day's totals, not Meta's own conversion."]),
    dict(name="daily_story_mix_rate", label="Mix versus rate (daily story)", domain="story",
         formula="Over segments (product, stage, campaign structure, market cluster, ICP, format, vendor, age, ad family): ROAS = sum(w_i r_i) with w_i the segment's share of spend and r_i its ad-attributed NC ROAS. mix_i = (w_i - w0_i) x (rbar_i - Rbar), rate_i = (r_i - r0_i) x wbar_i, where 0 is the pooled same-weekday baseline and bars are the mean of day and baseline; sum(mix_i + rate_i) equals the total move exactly.",
         source="analytics.daily_story_facts.facts",
         default_basis="ad-attributed NC ROAS (Upstack any-click 30-day attribution of new customers to Meta ads), a cut only",
         other_bases=["Facts path: cuts.<cut>.mix_rate, cuts.<cut>.rows[].effect and movers inside the JSON"],
         traps=["Mix = spend moving between segments with different returns; rate = the same segment earning more or less per dollar. Neither is a cause.", "Ad-attributed revenue is any-click and is not the headline: it does not add up to orders_daily new-customer revenue."]),
    dict(name="daily_story_pattern_lift", label="Pattern lift (daily story scoreboard)", domain="story",
         formula="For a change type: rise_rate = days whose next 1-3 days averaged more than 5% above the same-weekday baseline / days with that change; rise_lift = rise_rate - base_rise_rate (the same rate over all days). fall_lift likewise.",
         source="analytics.daily_story_pattern_scoreboard.rise_rate,base_rise_rate,rise_lift,n",
         default_basis="Upstack new-customer basis for NC ROAS and AOV; Meta-reported for CPM and CTR; conversion = actual new customers / Meta link clicks",
         other_bases=["The base rate (all days) sits in the same row as base_rise_rate and base_fall_rate"],
         traps=["Correlation, not cause: changes land together and the 1-3 day outcome windows of neighbouring days overlap.", "Only rows with n of 6 or more are listed; a high rate on a small n is not evidence."]),
]


# =====================================================================================================================================
# Example queries for the raw public tables (the analytics views carry their own above). One or two per table people actually query; twins say 'like X'.
# =====================================================================================================================================

_EXTRA_EXAMPLES = {
    "public.meta_ads": [
        ("Ads per status", "SELECT status, count(*) AS ads FROM meta_ads GROUP BY 1 ORDER BY 2 DESC;"),
        ("Ad sets of the most recently delivering campaign",
         "SELECT adset_name, count(*) AS ads FROM meta_ads WHERE campaign_id = (SELECT campaign_id FROM meta_campaigns ORDER BY last_day DESC NULLS LAST LIMIT 1) GROUP BY 1 ORDER BY 2 DESC;"),
    ],
    "public.meta_campaigns": [
        ("Campaigns by stage", "SELECT stage, count(*) AS campaigns, min(first_day) AS first_day, max(last_day) AS last_day FROM meta_campaigns GROUP BY 1;"),
    ],
    "public.meta_ad_lifetime": [
        ("Top 20 ads by all-time spend (Meta-reported)",
         "SELECT l.ad_id, a.ad_name, round(l.spend) AS spend, round(l.conv_value / NULLIF(l.spend, 0), 2) AS roas FROM meta_ad_lifetime l JOIN meta_ads a USING (ad_id) ORDER BY l.spend DESC LIMIT 20;"),
    ],
    "public.meta_family_map": [
        ("How Meta families matched tracker families", "SELECT coalesce(rule, '(unmatched)') AS rule, count(*) AS families FROM meta_family_map GROUP BY 1 ORDER BY 2 DESC;"),
    ],
    "public.tracker_rows": [
        ("Live tracker rows and unique ads per product",
         "SELECT p.slug, count(*) AS rows, count(DISTINCT t.family_key) AS unique_ads FROM tracker_rows t JOIN products p ON p.id = t.product_id WHERE t.deleted_at IS NULL GROUP BY 1 ORDER BY 2 DESC;"),
        ("Rows by status", "SELECT status, count(*) AS rows FROM tracker_rows WHERE deleted_at IS NULL GROUP BY 1 ORDER BY 1;"),
    ],
    "public.ad_tags": [
        ("Current tags by set, version and confidence", "SELECT tag_set, version, confidence, count(*) AS tags FROM ad_tags WHERE is_current GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;"),
    ],
    "public.taxonomy_lines": [
        ("Lines per product and cluster", "SELECT p.slug, l.cluster, count(*) AS lines FROM taxonomy_lines l JOIN products p ON p.id = l.product_id GROUP BY 1, 2 ORDER BY 1, 3 DESC;"),
    ],
    "public.line_ingredients": [
        ("Ingredients of the first lines", "SELECT l.line_code, l.pain_point, i.ingredient, i.attacks FROM line_ingredients i JOIN taxonomy_lines l ON l.id = i.line_id ORDER BY l.line_code, i.sort_order LIMIT 20;"),
    ],
    "public.ad_hooks": [
        ("Hook source and confidence mix", "SELECT hook_source, confidence, count(*) AS hooks FROM ad_hooks GROUP BY 1, 2 ORDER BY 3 DESC;"),
    ],
    "public.products": [
        ("The product list", "SELECT id, slug, name, tag_set, active FROM products ORDER BY sort_order;"),
    ],
    "public.ad_metrics": [
        ("Since-launch spend and Meta ROAS per product (first rows only, so each family counts once)",
         "SELECT p.slug, count(*) AS unique_ads, round(sum(m.spend_launch)) AS spend, round(sum(m.revenue_launch) / NULLIF(sum(m.spend_launch), 0), 2) AS roas "
         "FROM ad_metrics m JOIN products p ON p.id = m.product_id WHERE m.is_first_row GROUP BY 1 ORDER BY 3 DESC;"),
    ],
    "public.family_daily": [
        ("Daily spend by stage of the top-spending creative family",
         "SELECT day, stage, round(spend, 2) AS spend, purchases FROM family_daily WHERE family_key = (SELECT family_key FROM family_daily GROUP BY 1 ORDER BY sum(spend) DESC LIMIT 1) ORDER BY day DESC, stage LIMIT 30;"),
    ],
    "public.unmatched_meta": [
        ("Spend of Meta families that sit on no tracker row, by guessed product",
         "SELECT p.slug, count(*) AS families, round(sum(u.spend_30d)) AS spend_30d FROM unmatched_meta u JOIN products p ON p.id = u.product_id GROUP BY 1 ORDER BY 3 DESC;"),
    ],
    "public.upstack_attr_days": [
        ("Pulled days and how many new customers Upstack credits to Meta at campaign vs ad level",
         "SELECT day, campaigns, ad_rows, nc_campaigns, nc_ads FROM upstack_attr_days ORDER BY day DESC LIMIT 14;"),
    ],
    "public.upstack_attr_daily": [
        ("Top utm campaigns by new customers, last 7 pulled days",
         "SELECT campaign, sum(nc) AS new_customers, sum(nc_rev) AS nc_revenue FROM upstack_attr_daily WHERE day > (SELECT max(day) FROM upstack_attr_days) - 7 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;"),
    ],
    "public.upstack_unmapped_daily": [
        ("Credit that matched no Meta ad, by reason", "SELECT reason, count(*) AS rows, sum(nc) AS new_customers, sum(orders) AS orders FROM upstack_unmapped_daily GROUP BY 1 ORDER BY 2 DESC;"),
    ],
    "public.upstack_channel_daily": [
        ("Upstack's own Meta spend per day (the spend check)", "SELECT day, round(sum(spend), 2) AS spend FROM upstack_channel_daily GROUP BY 1 ORDER BY 1 DESC LIMIT 14;"),
    ],
    "public.upstack_google_campaigns": [
        ("Google campaigns by type", "SELECT campaign_type, count(*) AS campaigns FROM upstack_google_campaigns GROUP BY 1 ORDER BY 2 DESC;"),
    ],
    "public.upstack_google_channel_daily": [
        ("Google spend per day", "SELECT day, round(sum(spend), 2) AS spend, sum(clicks) AS clicks FROM upstack_google_channel_daily GROUP BY 1 ORDER BY 1 DESC LIMIT 14;"),
    ],
    "public.upstack_google_attr_daily": [
        ("Upstack-attributed Google new customers per day", "SELECT day, sum(nc) AS new_customers, sum(nc_rev) AS nc_revenue FROM upstack_google_attr_daily GROUP BY 1 ORDER BY 1 DESC LIMIT 14;"),
    ],
    "public.upstack_google_days": [
        ("First and last pulled Google attribution day", "SELECT min(day) AS first_day, max(day) AS last_day, count(*) AS days FROM upstack_google_days;"),
    ],
    "public.etl_runs": [
        ("Last 10 runs", "SELECT id, job, started_at, status, data_through FROM etl_runs ORDER BY id DESC LIMIT 10;"),
    ],
    "public.app_meta": [
        ("The facts the app reads about the data", "SELECT key, left(value, 80) AS value, updated_at FROM app_meta WHERE key NOT LIKE 'reliability:%' ORDER BY key;"),
    ],
    "public.proj_feed_platform": [
        ("Meta and Google spend per day since 2025 (the longest history)", "SELECT day, platform, round(spend, 2) AS spend FROM proj_feed_platform WHERE day >= current_date - 14 ORDER BY day DESC, platform;"),
    ],
    "public.proj_feed_nc_product": [
        ("Actual new-customer revenue by product, last 14 days", "SELECT product, round(sum(nc_rev)) AS nc_revenue, sum(nc_orders) AS new_customers FROM proj_feed_nc_product WHERE day >= current_date - 14 GROUP BY 1 ORDER BY 2 DESC;"),
    ],
    "public.proj_feed_product_reach": [
        ("Weekly unique outbound clicks per product", "SELECT period_key, product, unique_outbound_clicks FROM proj_feed_product_reach WHERE grain = 'week' ORDER BY period_start DESC, product LIMIT 12;"),
    ],
}
for _k, _ex in _EXTRA_EXAMPLES.items():
    assert _k in REL and not REL[_k].get("examples"), _k
    REL[_k]["examples"] = _ex


# =====================================================================================================================================
# Prose sections of docs/DATA_SCHEMA.md (hand-written; the table reference below them is generated)
# =====================================================================================================================================

INTRO_MD = """\
# Lonvera marketing database: data dictionary

> GENERATED by `scripts/gen_schema_doc.py` from `docs/schema/descriptions.py` and the live schema. Do not edit this file: edit the descriptions and regenerate.
> Machine-readable twin: `docs/schema.json`. Agent quick start: `docs/AGENT_DATA_GUIDE.md`.

## TLDR

Railway Postgres holding Lonvera's marketing, creative, order, inventory and planning data: Meta ads (ad x day since 2026-05-06, campaign x day since 2024-12-31), Google Ads
(campaign x day since 2024-03-25), Upstack attribution (which orders each ad / campaign gets credit for), the business's actual new-customer revenue per day, Amazon and site
traffic, the Creative Tracker with its tags and hooks, the weekly creative priorities and the production board, competitor ads, the Projection module's scenarios, actuals,
cohorts, purchase orders and stock, and the pacing feeds. People and agents read all of it through the **read-only `analytics` schema** (role `analytics_reader`, SELECT on
views only); everything else is the app's own storage. **Start with `select * from analytics.catalog`** (every view with its domain, description, grain, first / last day) and
`analytics.metric_glossary` (the canonical definition, default basis and traps of every headline metric: the Metrics section below).

| You want | Use |
|---|---|
| spend, impressions, clicks, purchases, ROAS per ad per day (Meta-reported AND Upstack-attributed) | `analytics.meta_ad_daily` |
| the same per ad set / per campaign per day | `analytics.meta_adset_daily` / `analytics.meta_campaign_daily` |
| an ad's name, campaign, ad set, status, product, all-time totals | `analytics.meta_ads` |
| Meta vs Google spend per day, Total NC ROAS | `analytics.platform_daily` + `analytics.orders_daily` |
| Google by campaign (type, spend, conversions, Upstack attribution) | `analytics.google_campaign_daily` |
| actual revenue and new customers per day (counted once) | `analytics.orders_daily` |
| which cluster / ICP / pain point / hook performs | `analytics.creative_tracker` |
| reach and frequency (account level only) | `analytics.meta_reach` |
| Meta before 2026-05-06 (campaign grain, from 2024-12-31) | `analytics.meta_campaign_daily_history` |
| is the data fresh, what failed | `analytics.data_coverage`, `analytics.etl_runs`, `analytics.pipeline_feeds`, `analytics.pipeline_alerts` |
| which view answers a question; what a metric means and how it goes wrong | `analytics.catalog`, `analytics.metric_glossary` |
| Meta / Google spend back to 2025-01-01; Meta spend and unique outbound clicks by product; bid caps | `analytics.platform_daily_history`, `analytics.meta_product_daily`, `analytics.meta_product_reach`, `analytics.meta_bids_daily` |
| last-click credit by source; any-click new customers by stage; orders no ad gets credit for | `analytics.attribution_source_daily`, `analytics.attribution_anyclick_daily`, `analytics.attribution_unmapped_daily` |
| Amazon: ads by campaign / ASIN, organic search queries (shares, not rank), orders by ASIN, traffic, settlement fees | `analytics.amazon_ads_campaign_daily`, `amazon_ads_asin_daily`, `amazon_search_queries_weekly`, `amazon_search_queries_monthly`, `amazon_orders_asin_daily`, `amazon_traffic_daily`, `amazon_market_baskets`, `amazon_repeat_purchases`, `amazon_settlement_fees_daily`, `amazon_asin_products` (domain `amazon`, docs/AMAZON_DATA.md) |
| actual new-customer revenue by product or hour; tax and refunds; Amazon; site traffic | `analytics.orders_by_product_daily`, `analytics.orders_hourly`, `analytics.orders_daily_detail`, `analytics.amazon_daily`, `analytics.site_traffic_daily` |
| creative family spend / results by basis, test to scale, copy and transcript | `analytics.creative_family_daily`, `analytics.creative_family_metrics`, `analytics.creative_stage_moves`, `analytics.creative_scaling_placements`, `analytics.creative_content`, `analytics.ad_transcripts` |
| tags, taxonomy lines, ingredients, hooks | `analytics.tracker_tags`, `analytics.tag_taxonomy_lines`, `analytics.tag_line_ingredients`, `analytics.tracker_hooks`, `analytics.tag_products` |
| the weekly priorities list (lines, status, score, hit rate, thresholds) | `analytics.priorities_concepts`, `analytics.priorities_snapshots`, `analytics.priorities_baselines`, `analytics.priorities_thresholds` |
| who is making which concept for which week | `analytics.production_assignments`, `analytics.production_formats`, `analytics.priorities_manual_concepts` |
| competitor ads and concepts | `analytics.competitor_ads`, `analytics.competitor_concepts`, `analytics.competitor_brands` |
| scenarios, levers, P&L inputs and QuickBooks actuals | `analytics.projection_scenarios`, `analytics.projection_levers`, `analytics.projection_series`, `analytics.projection_actuals`, `analytics.projection_pnl_*` |
| Klaviyo email / SMS: campaigns, flows, per-message sends, opens, clicks, orders, revenue, copy | `analytics.klaviyo_*` (domain `email`; created by migration 231 once the Klaviyo tables are on this database) |
| landing page copy versions and the ads that send people to each page | `analytics.landing_pages`, `landing_page_versions`, `landing_page_ads`, `landing_page_top_ads` (domain `landing_pages`; migration 231) |
| order-level Shopify orders and lines of the last 120 days (no customer data) | `analytics.shopify_orders`, `analytics.shopify_order_lines` (domain `orders`; migration 231) |
| purchase orders, FBA transfers, stock, units shipped, vendor terms | `analytics.inventory_*` |
| cohorts, retention curves (Live inputs and Goal), repeat revenue inputs | `analytics.cohorts_monthly`, `analytics.cohorts_curve_cumulative`, `analytics.cohorts_curve_goal` |
| pacing inputs: today so far, Amazon month to date, levers | `analytics.pacing_live_today`, `analytics.pacing_amazon_month_to_date`, `analytics.pacing_levers` |

Refresh: the `etl` service runs daily at 13:00 UTC (06:00 PDT / 05:00 PST) and loads data through **yesterday, Pacific**; the `feeds` service runs at 21:00 UTC (shadow feeds only).
Money is dollars. Days are calendar days (Meta: each ad account's own day; everything else Pacific).

## How to answer questions from this database (read before querying)

1. **Name the basis.** Three different "purchases" exist. *Meta-reported* (`meta_purchases`, `meta_revenue`: Meta's attribution, counts returning customers). *Upstack new customers*
   (`upstack_new_customers`, `upstack_new_customer_revenue`: first orders only, any-click 30-day attribution; this is the **NC** in NC ROAS / NC CPA). *Upstack all orders*
   (`upstack_orders`, `upstack_order_revenue`). Spend is Meta's in all three. Ratios are only comparable within a basis. Revenue is gross (tax included, before refunds).
2. **Spend adds across platforms. Purchases and revenue do not.** Meta and Google credit the same orders, so `own_purchases`, `own_revenue` and every `upstack_*` column of
   `platform_daily` must never be summed across platforms. **Total NC ROAS = `orders_daily.new_customer_revenue` (actual, counted once) / total spend of the same days**; use only days
   that Meta, Google and `orders_daily` all cover, and compute it as a ratio of sums (the examples under `analytics.platform_daily` do).
3. **Days.** Read `analytics.data_coverage` first. The last loaded day is yesterday (Pacific); today is never loaded (`meta_reach` day grain is the one exception and its last row is partial).
   "Last 30 days" means the 30 days ending at `last_day`, not at today. The last 3-7 days still move: Meta restates conversions for weeks (28 trailing days are re-pulled every night),
   Upstack and orders re-pull 7 days. Meta days are each account's own day (Main = Los Angeles, BackUp = New York); Google, Upstack and orders are Pacific.
4. **NULL is not zero.** `upstack_*` is NULL on days Upstack was not pulled (Meta: before 2026-05-06; Google: before 2025-05-13). A day with no orders is absent from `orders_daily`, not 0.
   Any-click attribution credits an ad on days it spent nothing, so a row with `spend = 0` and `upstack_* > 0` is normal.
5. **Counts and ratios.** Compute every ratio from sums (`sum(revenue)/sum(spend)`), never an average of daily ratios. Creative metrics in `creative_tracker` sit on the FIRST row of each
   ad family (other rows read 0): sum rows, never average them, and "unique ads" = `count(DISTINCT family_key)`. Meta coverage at ad / ad set grain starts 2026-05-06.

## Join map

| From | To | On |
|---|---|---|
| `analytics.meta_ad_daily` | `analytics.meta_ads` | `ad_id` |
| `analytics.meta_ads` | `analytics.creative_tracker` | `meta_ads.tracker_family_key = creative_tracker.family_key` (NULL = the ad is on no tracker row; a family can span several tracker rows, so filter `is_first_row` or use `DISTINCT`) |
| `analytics.platform_daily` | `analytics.orders_daily` | `day` (collapse platform first: `GROUP BY day`) |
| `analytics.meta_campaign_daily` / `meta_adset_daily` | `analytics.meta_ads` | `campaign_id` / `adset_id` |
| `public.ad_daily` | `public.meta_ads` | `ad_id` |
| `public.meta_ads` | `public.meta_campaigns` | `campaign_id` |
| `public.meta_ads` / `public.ad_daily` | `public.meta_family_map` | `meta_family_key` |
| `public.meta_family_map` | `public.tracker_rows` | `tracker_family_key = family_key` |
| `public.tracker_rows` | `public.products` | `product_id = id` |
| `public.ad_tags` | `public.tracker_rows` | `tracker_row_id = id` (one current row per tag_set: `WHERE is_current`) |
| `public.ad_tags` | `public.taxonomy_lines` | `line_id = id`; `taxonomy_lines.id = line_ingredients.line_id` |
| `public.ad_hooks`, `public.ad_metrics` | `public.tracker_rows` | `tracker_row_id = id` |
| `public.family_daily`, `family_content`, `creative_assets` | `public.tracker_rows` | `family_key` (tracker family) |
| `public.google_daily` | `public.upstack_google_campaigns` | `campaign_key = campaign_id` |
| `analytics.tracker_tags` / `analytics.tracker_hooks` / `analytics.creative_family_metrics` | `analytics.creative_tracker` | `tracker_row_id` (or `concept_id`, `family_key`) |
| `analytics.tracker_tags` | `analytics.tag_taxonomy_lines` | `line_id` (or `product_slug` + `line_code`); `analytics.tag_line_ingredients.line_id` the same |
| `analytics.priorities_concepts` | `analytics.tag_taxonomy_lines` | `product_slug` + `line_code` |
| `analytics.production_assignments` | `analytics.creative_tracker` | `tracker_concept_ids` (array) contains `concept_id` |
| `analytics.creative_stage_moves` / `creative_family_daily` | `analytics.creative_tracker` | `family_key` (filter `basis` first: the three bases are separate rows) |
| `analytics.projection_levers` / `projection_series` | `analytics.projection_scenarios` | `scenario_slug = slug` |
| `analytics.cohorts_curve_cumulative` | `analytics.cohorts_monthly` | `cohort_month` |
| `public.upstack_attr_daily` | Meta campaigns / ads | by NAME only (`campaign` = campaign name or raw id, `ad` = ad name); the build maps it into `ad_daily_upstack` |

## Gaps: what is NOT in the database

* **Meta before 2026-05-06 at ad or ad set grain.** Campaign grain goes back to 2024-12-31 (`meta_campaign_daily_history`); lifetime totals per ad cover the ad's whole life (`meta_ads.lifetime_*`).
* **Reach and frequency below account level.** `meta_reach` is account level; per product only unique outbound clicks exist (`proj_feed_product_reach`, not in analytics). None per ad, ad set or campaign.
* **Video and creative-quality metrics** (thruplays, hook / hold rate, watch time, quality rankings), **breakdowns** (age, gender, placement, device, country), **ad budgets and bids** per ad set
  (only daily summed bid totals in `proj_feed_bids`).
* **Google below campaign**: no ad group, ad, keyword, search term, device or geo. Upstack's Google attribution starts 2025-05-13.
* **Upstack attribution for Meta before 2026-05-06** (Upstack has it back to 2025-05-13; this database loaded it from the start of its Meta daily data).
* **TikTok and Bing spend**: Upstack returns none; no TikTok rows exist.
* **Customer- or order-level data**: no emails, customer ids, order lines, subscriptions. Orders exist only as daily totals; there is no product or platform split of actual new-customer revenue in `analytics`.
* **Projection OUTPUTS** (the computed P&L, cash, financing and inventory-burn results). The model is computed by the app on demand (`/api/projection/...`); the database holds its inputs and actuals
  (`projection_levers`, `projection_series`, `projection_actuals`, `cohorts_*`, `inventory_*`). A row you cannot find in `analytics.projection_*` is computed, not stored.
* **Refund-adjusted revenue** except as `orders_daily_detail.refunds`, **margins, COGS beyond the typed unit costs, CAC by cohort beyond `cohorts_monthly.cac`**.
* **Email / SMS (Klaviyo) and TikTok Shop** are not in the database. Amazon sales are in `analytics.amazon_daily` (a projection feed), site traffic in `analytics.site_traffic_daily`.
* **Creative files**: image bytes only in `public.creative_assets` (not exposed); the copy Meta actually runs is `analytics.creative_content`.
* **Who changed what**: every audit trail (projection, supply, upload, launch) names people and is not exposed.

Migration 231 creates the views for Klaviyo (`klaviyo_daily`, `klaviyo_campaigns`, `klaviyo_flows`, `klaviyo_flow_messages`, `klaviyo_message_content`, `klaviyo_message_performance`), landing pages
(`landing_pages`, `landing_page_versions`, `landing_page_ads`, `landing_page_ad_checks`, `landing_page_top_ads`) and Shopify orders (`shopify_orders`, `shopify_order_lines`) **only when those tables exist**
(they ship on separate branches); until then `analytics.catalog` does not list them and this reference does not describe them. They carry their source tables' COMMENTs. Left out of them:
`shopify_orders.customer_id` and `.tags`, every `raw` json column, `landing_page_versions.blocks / images` and `landing_page_ads.raw_url`.

## Excluded from the `analytics` schema, and why

| Relation | Why it is not in `analytics` |
|---|---|
| `feed_oauth` | encrypted ExpandFi refresh token (a credential); never |
| `api_keys` | key hashes of the scoped API keys; never |
| `creative_priorities_vendor_links`, `creative_priorities_views` | vendor link tokens and saved-view tokens (plus a payload); never |
| `schema_migrations`, `app_meta` | migration bookkeeping and internal keys |
| login / session | the app authenticates with environment-held users (`APP_USERS`) and signed cookies; there are no auth tables to expose |
| `proj_audit`, `proj_scenario_history`, `proj_supply_history`, `proj_upload_history`, `proj_cohort_uploads`, `proj_units_loads`, `creative_launch_audit` | audit and upload trails that name people (actor, undone_by, saved_by): not exposed. The same person columns (`created_by`, `updated_by`, `requested_by`, `saved_by`, `changed_by`, `updated_by` on the levers) are left out of the views that read otherwise safe tables |
| `creative_launch_*` (settings, pages, vendor prefixes, drafts, jobs, ads, adsets) | the dark Meta-write path: Page and account ids, plans and jobs |
| `creative_assets` | image bytes |
| `proj_feedx_*` | shadow twins of the feeds, only there to be compared with `proj_feed_*` (read those through the views) |
| `proj_scenarios.params`, `creative_priorities_snapshots.rows / thresholds / baselines / stats`, `ad_tags.extra`, `proj_amazon_mtd_snapshots.projected_days`, `proj_po.link` | raw JSON blobs or free links: the scenario levers and the snapshot rows are exposed flattened (`projection_levers`, `priorities_*`), the rest is left out |
| `proj_pacing_setting`, `competitor_concept_class`, `competitor_brands.last_error` | internal rules and error text; the classification itself is in `competitor_concepts` |
| `tracker_rows`, `ad_daily*`, `meta_*`, `upstack_*` (other than unmapped), `google_daily`, `family_daily*`, `ad_metrics*`, `unmatched_meta*`, `creative_stage_moves*` | raw sources of the views; the views are the supported surface |

The `analytics.etl_runs` view exposes `stats` (job counters) and the first 500 characters of `error` (scrubbed of tokens).
`analytics.creative_tracker.asset_url` is a Google Drive link; opening it is governed by Drive permissions, not by this database.
"""

METRICS_INTRO_MD = """\
The canonical definition of every headline metric, taken from the code (the file is named in `Source` or in a trap). Also `analytics.metric_glossary` (SQL) and the `metrics` key of `docs/schema.json`
(a list of `{name, label, formula, source, default_basis, other_bases, traps, domain}`). `source` is `analytics.<view>.<column(s)>`: columns of one view are comma-joined, views are separated by `; `.
Read the default basis before quoting a number and name the basis in the answer.
"""

TABLE_REFERENCE_INTRO_MD = """\
## Reference

One section per relation, grouped as in the table below. Column types, nullability and keys come from the live schema; meaning, units and caveats from
`docs/schema/descriptions.py`. For views, nullability is not tracked by Postgres: read the text. 'like X' means the columns have the same meaning as in X.
"""


ANALYTICS_SCHEMA_COMMENT = (
    "Read-only views over Lonvera's data: marketing (Meta, Google), attribution, orders, creative, tags, priorities, production, projection, inventory, cohorts, pacing, competitors and pipeline. "
    "Start with catalog (every view with its domain, description, grain, first / last day), metric_glossary (the canonical metric definitions) and data_coverage (what is loaded through which day); read "
    "docs/AGENT_DATA_GUIDE.md. Rules: (1) name the basis: meta_* = Meta-reported, upstack_new_customer* = Upstack first orders (NC), upstack_order* = all Upstack-credited orders; "
    "(2) spend adds across Meta and Google, purchases and revenue never do; Total NC ROAS = orders_daily.new_customer_revenue / total spend of the same days; "
    "(3) the last loaded day is yesterday Pacific and the last 3-7 days still move; (4) upstack_* is NULL, not 0, on days Upstack was not pulled; "
    "(5) ratios from sums; creative_tracker metrics sit on the first row of each ad family."
)

rel("public.creative_launch_settings", group="creative", kind="table",
    purpose='Per-product launch settings for creating paused Meta ads from /priorities production: where the ads go (account, campaign, template ad set), the budget and bid for a new ad set, the default Page, pixel, landing page, URL tags, call to action and the default ad copy.',
    grain='one row per product (product is the primary key)', source='the launch module (app/priorities_launch.py) and the launch worker (settings screen)',
    refresh='on demand (edited by a person)', coverage="from the day the migration was deployed",
    caveats=['No rows are seeded: the account and campaign ids are entered in the app before anything can launch.', 'Amounts are stored in cents; a launch always creates ads and ad sets PAUSED.'],
    cols={'product': 'Product the settings apply to (primary key), spelled as in creative_priorities_assignments.product.', 'account_id': 'Meta ad account id the ads are created in.', 'campaign_id': 'Campaign the new ad sets go into.', 'template_adset_id': 'Existing ad set that a new ad set is cloned from (targeting, optimization, attribution).', 'daily_budget_cents': 'Daily budget of a new ad set, in cents (default 10000 = $100).', 'bid_amount_cents': 'Bid amount of a new ad set, in cents (default 6800 = $68).', 'default_page_id': 'Facebook Page the ads run as when the draft does not name one.', 'pixel_id': 'Meta pixel id for the product.', 'landing_url': 'Landing page link the ads point to.', 'url_tags': 'URL parameters appended to the link; NULL means the built-in default tags.', 'cta_image': 'Call to action for image ads (default LEARN_MORE).', 'cta_video': 'Call to action for video ads (default SHOP_NOW).', 'primary_texts': 'Default primary texts, used when neither the draft nor the best existing ad supplies any.', 'headlines': 'Default headlines, used when neither the draft nor the best existing ad supplies any.', 'description': 'Default link description; NULL when none.', 'updated_at': 'When the settings were last changed.', 'updated_by': 'Signed-in user who last changed them; NULL when none.'})

rel("public.creative_launch_pages", group="creative", kind="table",
    purpose='The Facebook Pages (with their Instagram accounts) an ad can run as when launching from /priorities production.',
    grain='one row per Page (page_id is the primary key)', source='the launch module (app/priorities_launch.py) and the launch worker (settings screen)',
    refresh='on demand (edited by a person)', coverage="from the day the migration was deployed",
    caveats=['name_token is the short text put at the end of the ad name for ads on this Page.'],
    cols={'page_id': 'Facebook Page id (primary key).', 'name': 'Page name shown in the launch screen.', 'instagram_user_id': 'Instagram account id linked to the Page; NULL when none.', 'name_token': 'Short token added to the ad name for this Page (for example lonvera).', 'active': 'True while the Page may be chosen; false hides it from the picker.'})

rel("public.creative_launch_vendor_prefixes", group="creative", kind="table",
    purpose='Concept ID prefix per vendor, used when the app mints Concept IDs for launched creative (for example Lonvera-BeefLiver-Static-A12).',
    grain='one row per vendor (vendor is the primary key)', source='the launch module (app/priorities_launch.py) and the launch worker (settings screen); seeded by migration 203',
    refresh='on demand (edited by a person)', coverage="from the day the migration was deployed",
    caveats=['Seeded with Madre = Lonvera, Victoria = Victoria, 10xROAS = 10x, Internal = Internal; a vendor with no row falls back to its name without punctuation.'],
    cols={'vendor': 'Vendor name (primary key), spelled as in creative_priorities_assignments.vendor.', 'prefix': 'Text put at the start of the Concept ID.'})

rel("public.creative_launch_drafts", group="creative", kind="table",
    purpose='The editable launch form for one assignment: the folder or file link, the tag values (Style, Fidelity, Type, Proof, Page) and the copy overrides, saved as the person types.',
    grain='one row per assignment (assignment_id is the primary key)', source='the launch module (app/priorities_launch.py) and the launch worker (launch screen)',
    refresh='on demand (saved while a person edits)', coverage="from the day the migration was deployed",
    caveats=['fields is free-form JSON validated on read; an unknown key is ignored.'],
    cols={'assignment_id': 'creative_priorities_assignments.id this draft belongs to (primary key, foreign key).', 'fields': 'The draft as JSON (link, style, fidelity, type, proof, page, primary texts, headlines).', 'updated_at': 'When the draft was last saved.', 'updated_by': 'Signed-in user who last saved it; NULL when none.'})

rel("public.creative_launch_jobs", group="creative", kind="table",
    purpose='One prepare or launch run for an assignment, with the exact plan it was approved against and its outcome.',
    grain='one row per run (id is the primary key); at most one open (queued or running) run per assignment', source='the launch module (app/priorities_launch.py) and the launch worker',
    refresh='on demand (a person queues a run; the worker updates it)', coverage="from the day the migration was deployed",
    caveats=['A launch only runs the plan whose plan_hash the person approved.', 'dry_run true means nothing is sent to Meta beyond validation.'],
    cols={'id': 'Primary key (bigserial).', 'assignment_id': 'creative_priorities_assignments.id the run is for (foreign key).', 'kind': "'prepare' (list files, mint IDs, build the plan) or 'launch' (create the paused ads).", 'state': "'queued' | 'running' | 'done' | 'failed' | 'cancelled'.", 'plan': 'The plan as JSON (one entry per ad plus the destination and PAUSED status).', 'plan_hash': 'sha256 of the canonical plan JSON; the launch must carry the same hash that was approved.', 'dry_run': 'True when the run only validates and sends nothing live.', 'requested_by': 'Signed-in user who queued the run.', 'error': 'Scrubbed error text when the run failed; NULL otherwise.', 'created_at': 'When the run was queued.', 'started_at': 'When the worker picked it up; NULL until then.', 'finished_at': 'When it finished; NULL until then.'})

rel("public.creative_launch_ads", group="creative", kind="table",
    purpose='One row per file launched as an ad, with its Concept ID, ad name and the Meta ids created step by step, so a half-finished run can be resumed without duplicating anything.',
    grain='one row per assignment x Drive file; Concept IDs are unique across all rows', source='the launch module (app/priorities_launch.py) and the launch worker',
    refresh='on demand (the worker updates the row after each Meta step)', coverage="from the day the migration was deployed",
    caveats=['Concept IDs are minted once and never reused.', 'state moves planned, media_uploaded, creative_created, created; failed keeps the scrubbed error.'],
    cols={'id': 'Primary key (bigserial).', 'assignment_id': 'creative_priorities_assignments.id (foreign key).', 'job_id': 'creative_launch_jobs.id of the run that last touched the row (foreign key); NULL when none.', 'drive_file_id': 'Google Drive file id of the source file.', 'file_name': 'Name of the source file.', 'kind': "'image' or 'video'.", 'concept_id': 'Concept ID minted for the ad (unique).', 'ad_name': 'Ad name sent to Meta (matches the Creative Tracker Name column format).', 'state': "'planned' | 'media_uploaded' | 'creative_created' | 'created' | 'failed'.", 'meta_image_hash': 'Meta image hash after upload; NULL for videos or before upload.', 'meta_video_id': 'Meta video id after upload; NULL for images or before upload.', 'meta_creative_id': 'Meta ad creative id; NULL until created.', 'meta_ad_id': 'Meta ad id; NULL until created.', 'meta_adset_id': 'Ad set the ad was created in; NULL until created.', 'error': 'Scrubbed error text when the step failed; NULL otherwise.', 'created_at': 'When the row was planned.', 'updated_at': 'When the row last changed.'})

rel("public.creative_launch_adsets", group="creative", kind="table",
    purpose='The paused ad set the launch created for a product, vendor and week, so later launches of the same combination reuse it.',
    grain='one row per product x vendor x week (the primary key)', source='the launch module (app/priorities_launch.py) and the launch worker',
    refresh='on demand (written when an ad set is first created)', coverage="from the day the migration was deployed",
    caveats=['Ad set name is <Product>_<Vendor>_<week>_Production.'],
    cols={'product': 'Product (part of the primary key).', 'vendor': 'Vendor (part of the primary key).', 'for_week': 'Monday of the week the work is for (part of the primary key).', 'meta_adset_id': 'Meta ad set id created for the combination.', 'created_at': 'When the ad set was created.'})

rel("public.creative_launch_audit", group="creative", kind="table",
    purpose='Append-only log of launch actions: settings changed, plan prepared, launch requested, ads created, cancelled.',
    grain='one row per action (id is the primary key)', source='the launch module (app/priorities_launch.py) and the launch worker',
    refresh='on demand (a row per action)', coverage="from the day the migration was deployed",
    caveats=['Never updated or deleted.'],
    cols={'id': 'Primary key (bigserial).', 'at': 'When the action happened.', 'actor': 'Signed-in user or worker name.', 'action': 'Short action name.', 'assignment_id': 'Assignment the action concerns; NULL when none. No foreign key, so the log outlives its subject.', 'detail': 'Action details as JSON, never containing a token.'})

rel("public.api_keys", group="ops", kind="table",
    purpose="Scoped API keys for the Tag Explorer API: one row per key handed to a colleague. A key can read everything the app serves and can only assign items on the production board.",
    grain="one row per key (id is the primary key; name and key_hash are each unique)", source="python -m etl.api_keys create / revoke (etl/api_keys.py); last_used_at is touched by the web app (app/api_keys.py)",
    refresh="on demand (a person creates or revokes a key; last_used_at moves at most once a minute while a key is in use)", coverage="from the day the migration was deployed",
    caveats=["Only the sha256 hash of the key is stored. The key itself is shown once when it is made and cannot be recovered; make a new key and revoke the old one instead.",
             "A revoked key is never deleted, so the history stays. Assignments made with a key carry created_by / updated_by 'key:<name>' in creative_priorities_assignments."],
    cols={"id": "Primary key (bigserial).", "name": "Short name of the key, for example the colleague it was given to (unique). Shown in the audit columns as key:<name>.",
          "key_hash": "sha256 hex digest of the full key (unique). The key is looked up by this hash.", "prefix": "First 8 characters of the key ('lvk_' plus 4), for display only.",
          "scope": "What the key may do. 'read_assign' (the only scope): every GET, plus POST /api/priorities/assignments and PATCH /api/priorities/assignments/<id>.",
          "created_at": "When the key was made.", "created_by": "Operating-system user who ran the create command; NULL when unknown.",
          "last_used_at": "When the key last made a request (updated at most once a minute); NULL while unused.", "revoked_at": "When the key was revoked; NULL while active. A revoked key is refused with 401."})


rel("public.shopify_orders", group="orders", kind="table",
    purpose="Order-level Shopify orders of the last 120 days (rolling), one row per order, with money, status, source, discount codes and ship-to country / region. The place to answer order-level questions (refunds, rebills, gifts, discounts) that the daily roll-ups hide.",
    grain="one row per Shopify order (order_id is the primary key)",
    source="ExpandFi warehouse, merchant 1452, table shopify_orders (read-only), loaded by python -m etl.shopify_orders (etl/shopify_orders.py); fields come from the order's raw Shopify JSON",
    refresh="nightly (feeds service 21:00 UTC): the trailing 14 days are re-pulled every run, the whole 120 days every Sunday (Pacific) and on the first load; orders older than 120 days are deleted",
    coverage="orders processed in the last 120 Pacific days, including today so far; the warehouse trails Shopify by minutes to hours",
    caveats=["No personal data is stored: no email, name, address or phone. customer_id is the Shopify customer id (NULL when the order has none); the ship-to is country and region only.",
             "order_date is the Pacific day of processed_at. The warehouse's own created_at is the sync time of a September backfill and is NOT used.",
             "is_new_customer = this is the customer's first Shopify order EVER (customer key = Shopify customer id, else the lower-cased email, as in the new-customer revenue feed), judged over the whole warehouse history, not only the 120 days.",
             "refunded_amount = total_price - current_total_price (never below 0): Shopify's current total already nets refunds and order edits, and the warehouse's refund transactions are empty for many refunds. It is a net-down amount, not a refund ledger.",
             "Orders restated after they fall out of the trailing 14 days are picked up by the Sunday full 120-day pull, so a late refund can lag up to a week.",
             "is_rebill = source_name is subscription_contract_checkout_one. is_trybe_gift = the order carries the tag trybe (the $0 gift orders). Gift, test and cancelled orders are kept and flagged, never dropped; filter on the flags.",
             "Revenue by product is NOT the sum of total_price by product: use shopify_order_lines.line_net for a split."],
    cols={"order_id": "Shopify order id (primary key). Not the ExpandFi row id.", "order_number": "Human-readable order number as the store shows it.",
          "processed_at": "When the order was processed (UTC instant).", "order_date": "Pacific calendar day of processed_at; the day to group by.",
          "customer_id": "Shopify customer id; NULL when the order has none. No other customer data is stored.",
          "is_new_customer": "True when this is the customer's first Shopify order ever.", "currency": "Order currency code (shop currency).",
          "subtotal_price": "Order subtotal after discounts, before shipping and tax.", "total_discounts": "Sum of all discounts on the order.",
          "shipping_price": "Shipping charged (shop money).", "total_tax": "Tax charged.", "total_price": "Order total at checkout: subtotal + shipping + tax.",
          "current_total_price": "Order total after refunds and edits, as Shopify reports it now.",
          "refunded_amount": "total_price minus current_total_price, never below 0 (see the caveat).",
          "financial_status": "Shopify financial status (paid, refunded, partially_refunded, voided, ...).", "fulfillment_status": "Shopify fulfillment status; NULL = unfulfilled.",
          "source_name": "Shopify source name (web, subscription_contract_checkout_one for rebills, tiktok, or an app id).", "app_id": "Shopify app id that created the order; NULL when none.",
          "tags": "Order tags, comma separated, as Shopify stores them.", "discount_codes": "Discount codes used, comma separated; NULL when none.",
          "ship_country": "Ship-to ISO country code; NULL when the order has no shipping address.", "ship_region": "Ship-to province / state code; NULL when none.",
          "cancelled_at": "When the order was cancelled; NULL when it was not.", "is_cancelled": "True when cancelled_at is set.", "is_test": "True for a Shopify test order.",
          "is_rebill": "True for a subscription rebill (source_name subscription_contract_checkout_one).", "is_trybe_gift": "True when the order has the tag trybe (free sample / gift orders).",
          "line_count": "Number of line items stored in shopify_order_lines for this order.", "loaded_at": "When this row was last written by the loader."})

rel("public.shopify_order_lines", group="orders", kind="table",
    purpose="Line items of shopify_orders: one row per product line with SKU, product bucket, quantity, price and discount.",
    grain="one row per order line (order_id, line_no is the primary key; line_no counts from 1 in the order's own line order)",
    source="the line_items array of the same ExpandFi shopify_orders rows, loaded together with shopify_orders by python -m etl.shopify_orders",
    refresh="with shopify_orders (nightly 21:00 UTC); a re-pulled order has its lines replaced, an order that leaves the 120-day window takes its lines with it",
    coverage="the same orders as shopify_orders",
    caveats=["product is mapped from the SKU prefix: LV-VBULL = VitaBull, LV-BLC180 and DPCD1-GN180001 = BeefLiver, DPCD1-GN187001 = Boneflex, LV-VBELLE = VitaBelle, DPCD1-GN179001 = VitaFlow, anything else = Other.",
             "line_net = max(price x quantity - line_discount, 0); the order's total_price also carries shipping and tax and order-level discounts, so the lines do not add up to it."],
    cols={"order_id": "Shopify order id; foreign key to shopify_orders.order_id (cascade delete).", "line_no": "Position of the line in the order, from 1.",
          "sku": "SKU as sold; NULL when the line has none.", "product_name": "Line title as it shows on the order.", "product": "Product bucket mapped from the SKU (VitaBull, BeefLiver, Boneflex, VitaBelle, VitaFlow, Other).",
          "quantity": "Units on the line.", "price": "Unit price before discount.", "line_discount": "Total discount on the line (all units).", "line_net": "price x quantity minus line_discount, never below 0."})
rel("public.klaviyo_campaigns", group="ops", kind="table",
    purpose="Dimension: one row per Klaviyo campaign (email and SMS) with its name, status, send time and subject. Names the parent ids of klaviyo_daily.",
    grain="one row per campaign (id is the primary key; Klaviyo campaign id)", source="Klaviyo API GET /campaigns/ with campaign-messages, both channels, via python -m etl.klaviyo (etl/klaviyo.py)",
    refresh="nightly with the daily ETL (whole list re-upserted every run)", coverage="every campaign Klaviyo lists, regardless of the metric window",
    caveats=["raw holds the Klaviyo attributes and the campaign's messages (id, label, subject); an A/B campaign has several messages and subject is the first one's.",
             "A campaign that sent in the window but is no longer listed shows in klaviyo_daily as '(unknown campaign message ...)'."],
    cols={"id": "Klaviyo campaign id (primary key).", "name": "Campaign name.", "channel": "'email' or 'sms'.", "status": "Klaviyo status text, for example Sent, Draft, Queued without Recipients.",
          "send_time": "Scheduled send time (UTC); NULL when not scheduled.", "subject": "Subject line of the first message; NULL for SMS and drafts without one.",
          "created_at": "When the campaign was created in Klaviyo.", "updated_at": "When the campaign was last edited in Klaviyo.", "raw": "Klaviyo attributes plus the campaign's messages, as JSON.",
          "loaded_at": "When this row was last written."})

rel("public.klaviyo_flows", group="ops", kind="table",
    purpose="Dimension: one row per Klaviyo flow with its name, status and trigger type. Names the parent ids of klaviyo_daily.",
    grain="one row per flow (id is the primary key; Klaviyo flow id)", source="Klaviyo API GET /flows/ via python -m etl.klaviyo (etl/klaviyo.py)",
    refresh="nightly with the daily ETL (whole list re-upserted every run)", coverage="every flow Klaviyo lists (draft, manual and live), regardless of the metric window",
    caveats=["raw is the Klaviyo attribute object as returned."],
    cols={"id": "Klaviyo flow id (primary key).", "name": "Flow name.", "status": "draft, manual or live.", "trigger_type": "What starts the flow, for example Metric, List, Segment, Date.",
          "created_at": "When the flow was created.", "updated_at": "When the flow was last edited.", "raw": "Klaviyo attributes as JSON.", "loaded_at": "When this row was last written."})

rel("public.klaviyo_flow_messages", group="ops", kind="table",
    purpose="Dimension: one row per message (email or SMS step) inside a Klaviyo flow, with its name and subject. Names the message_id of klaviyo_daily flow rows.",
    grain="one row per flow message (id is the primary key; Klaviyo flow message id)", source="Klaviyo API GET /flows/{id}/flow-actions/ then /flow-actions/{id}/flow-messages/ via python -m etl.klaviyo (etl/klaviyo.py)",
    refresh="nightly with the daily ETL, for live flows and flows that had activity in the pulled window", coverage="messages of every live flow and of flows with sends, opens, clicks or orders in any pulled window",
    caveats=["Messages of draft or manual flows that were silent in every pulled window are not fetched, so they are missing here.", "Messages deleted from a flow are not listed; their old metrics in klaviyo_daily keep the message_id with no name."],
    cols={"id": "Klaviyo flow message id (primary key).", "flow_id": "Parent flow (klaviyo_flows.id). No foreign key.", "name": "Message name in the flow builder.",
          "channel": "'email' or 'sms' as Klaviyo reports it, lowercased.", "subject": "Email subject line; NULL for SMS.", "raw": "Klaviyo attributes plus the flow action id, as JSON.", "loaded_at": "When this row was last written."})

rel("public.klaviyo_daily", group="ops", kind="table",
    purpose="Klaviyo email and SMS performance by Pacific day, campaign or flow, message and channel: sends, delivered, opens, clicks, unsubscribes and Klaviyo-attributed orders and revenue.",
    grain="one row per day (Pacific) x source ('campaign' | 'flow') x parent_id x message_id x channel (that is the primary key; message_id is '' where Klaviyo gives none)",
    source="Klaviyo metric-aggregates (Received / Bounced / Opened / Clicked / Marked as Spam / Unsubscribed events by $flow and $message, SMS equivalents, and Placed Order - Upstackified by $attributed_flow / $attributed_message) via python -m etl.klaviyo (etl/klaviyo.py)",
    refresh="nightly with the daily ETL; the trailing 14 complete Pacific days are replaced each run so late opens, clicks and orders land", coverage="complete Pacific days through yesterday; history from the first backfill (120 days at launch)",
    caveats=["unique_opens and unique_clicks are per-day uniques: do not add them across days or messages; sums run 20-55% above Klaviyo's single-window uniques.",
             "orders and revenue are Klaviyo-attributed conversions of the metric 'Placed Order - Upstackified' (S6xLbC, the CheckoutChamp / Upstack server feed), dated by the day the order happened, not the day the message was sent; Shopify's own Placed Order metric reads about 10% lower.",
             "Clicks exclude clicks Klaviyo flags as bots. sends = delivered + bounced.",
             "Klaviyo does not expose SMS unsubscribes per flow, and campaign SMS unsubscribes are matched by campaign name with no message (message_id ''); about 2K keyword-STOP unsubscribes have no campaign and are not in this table.",
             "SMS rows have no opens, unique_opens or spam_complaints (0).",
             "Sends, delivered and revenue reconcile within about 3% of Klaviyo's own campaign and flow value reports."],
    cols={"day": "Calendar day in America/Los_Angeles (event date).", "source": "'campaign' or 'flow'.", "parent_id": "Klaviyo campaign id or flow id (klaviyo_campaigns.id / klaviyo_flows.id). No foreign key.",
          "message_id": "Klaviyo message id (campaign message or flow message); '' when the row has none (SMS unsubscribes by campaign name).", "channel": "'email' or 'sms'.",
          "parent_name": "Name of the campaign or flow when loaded (denormalised); '(unknown ...)' when Klaviyo no longer lists it.",
          "sends": "Messages Klaviyo tried to send that day (delivered plus bounced).", "delivered": "Messages delivered.", "bounced": "Emails bounced or SMS that failed to deliver.",
          "opens": "Email opens (includes Apple Mail Privacy Protection machine opens); 0 for SMS.", "unique_opens": "Distinct recipients who opened that day (per-day unique, not additive); 0 for SMS.",
          "clicks": "Clicks, bot clicks excluded.", "unique_clicks": "Distinct recipients who clicked that day (per-day unique, not additive).",
          "unsubscribes": "Unsubscribes counted that day (email: clicked-to-unsubscribe events; SMS: campaign-level only).", "spam_complaints": "Emails marked as spam; 0 for SMS.",
          "orders": "Orders Klaviyo attributes to the message, dated by order day.", "revenue": "Revenue of those orders in dollars (Placed Order - Upstackified value), dated by order day.",
          "loaded_at": "When this row was last written."})

rel("public.klaviyo_message_content", group="ops", kind="table",
    purpose="The copy of every Klaviyo message (campaign message or flow message) that had activity in a pulled window, or that sits in a live flow: subject, preview text, full email HTML, readable body text and the message links. Joins to klaviyo_daily on message_id.",
    grain="one row per message (message_id is the primary key; Klaviyo campaign message id or flow message id)",
    source="Klaviyo API: GET /campaign-messages/{id}/template/ and /flow-messages/{id}/template/ (email HTML), the message definitions from GET /campaigns/ and /flow-actions/{id}/flow-messages/ (subject, preview, sender, SMS body), via python -m etl.klaviyo (etl/klaviyo.py)",
    refresh="nightly with the daily ETL, for messages with activity in the trailing 14 days and every message of a live flow", coverage="messages with activity in any pulled window (120 days at launch) plus live-flow messages; not every message Klaviyo has ever held",
    caveats=["Only the latest copy is kept: when a message is edited the row is overwritten and fetched_at moves (content_hash changes); the history of edits is not kept, so old performance reads against the current copy.",
             "Messages whose template Klaviyo no longer returns (deleted campaigns or templates) have a row with NULL html and body_text and raw.fetch_error set.",
             "body_text is extracted from the HTML (head, scripts and styles dropped, link text kept, whitespace collapsed); image-only emails have little or no text, and image_count says how many real images the email carries. For SMS body_text is the message body and html is NULL.",
             "image_count excludes tracking pixels and 1-pixel images."],
    cols={"message_id": "Klaviyo message id (primary key); equals klaviyo_daily.message_id.", "source": "'campaign' or 'flow'.", "parent_id": "Campaign id or flow id of the message. No foreign key.", "channel": "'email' or 'sms'.",
          "subject": "Email subject line; NULL for SMS.", "preview_text": "Email preview text; NULL for SMS or when none is set.", "from_name": "Sender name of the email.", "from_email": "Sender address of the email.",
          "template_id": "Klaviyo template id the email renders from; NULL for SMS or when the template was not returned.", "editor_type": "Klaviyo editor of the template: SIMPLE (plain text email), CODE, DRAG_AND_DROP, USER_DRAFTED; NULL for SMS.",
          "html": "Full template HTML of the email; NULL for SMS or when not returned.", "body_text": "Readable plain text of the email (or the SMS body); NULL when nothing could be fetched or the email is images only.",
          "image_count": "Real images in the email (tracking pixels excluded); 1 for an SMS with a picture.", "link_urls": "Distinct http(s) links in the message, in order of appearance.",
          "content_hash": "sha256 of the HTML (email) or body (SMS); changes when the message is edited.", "fetched_at": "When the content last changed in our copy (moves only when content_hash changes).",
          "raw": "The message definition as Klaviyo returns it (without the body / html), plus fetch_error when the template could not be fetched."})

rel("public.klaviyo_message_performance", group="ops", kind="view",
    purpose="One row per Klaviyo message with its copy (subject, preview text, body text) and its totals and rates over everything in klaviyo_daily: the table to read which copy earns.",
    grain="one row per message_id (campaign or flow message with at least one klaviyo_daily row)", source="klaviyo_daily (grouped by message_id) joined to klaviyo_message_content",
    refresh="live (a view over klaviyo_daily; moves with the nightly Klaviyo load)", coverage="the days loaded in klaviyo_daily (120 days at launch, then trailing); first_send_day is the first loaded day with sends, not the message's real first send",
    caveats=["unique_opens_daily_sum and unique_clicks_daily_sum are sums of per-day uniques, not window uniques: a person who opens on two days counts twice. open_rate_daily_uniques and click_rate_daily_uniques are built on them and read a little high; use them to compare messages, not as Klaviyo's reported rates.",
             "Orders and revenue are Klaviyo-attributed conversions of 'Placed Order - Upstackified', dated by the order day, so a message can show revenue with no sends when it sent before the loaded window.",
             "Rates are NULL when delivered is 0. SMS messages have no opens. Messages without fetched copy have NULL subject / body_text and has_copy false.",
             "SMS campaign unsubscribes are matched to the campaign, not the message, so they are not in this view."],
    cols={"message_id": "Klaviyo message id.", "source": "'campaign' or 'flow'.", "parent_id": "Campaign id or flow id.", "channel": "'email' or 'sms'.", "parent_name": "Campaign or flow name.",
          "subject": "Email subject (current copy); NULL for SMS or when not fetched.", "preview_text": "Email preview text (current copy).", "body_text": "Readable body text (current copy).",
          "image_count": "Real images in the email.", "editor_type": "Klaviyo template editor type.",
          "first_send_day": "First loaded day with sends.", "last_send_day": "Last loaded day with sends.", "sends": "Sum of sends.", "delivered": "Sum of delivered.", "bounced": "Sum of bounced or failed.",
          "opens": "Sum of opens (includes machine opens).", "unique_opens_daily_sum": "Sum of per-day unique opens (not a window unique).", "clicks": "Sum of clicks, bots excluded.",
          "unique_clicks_daily_sum": "Sum of per-day unique clicks (not a window unique).", "unsubscribes": "Sum of unsubscribes.", "spam_complaints": "Sum of spam complaints.",
          "orders": "Sum of attributed orders.", "revenue": "Sum of attributed revenue in dollars.", "open_rate_daily_uniques": "unique_opens_daily_sum / delivered; NULL when delivered is 0.",
          "click_rate_daily_uniques": "unique_clicks_daily_sum / delivered; NULL when delivered is 0.", "orders_per_delivered": "orders / delivered; NULL when delivered is 0.",
          "revenue_per_delivered": "revenue / delivered in dollars; NULL when delivered is 0.", "has_copy": "True when body_text was fetched for the message."})
rel("public.landing_pages", group="meta", kind="table",
    purpose="Every landing page (sales page, advertorial, product page, funnel step) that at least one Meta ad sends people to, with when we last read it.",
    grain="one row per normalised page address (id is the primary key; url is unique)", source="etl/landing_pages.py: the destination links found on Meta ad creatives",
    refresh="each run of python -m etl.landing_pages (not scheduled yet; see docs/LANDING_PAGES.md)", coverage="pages of ads that had spend in the window of the first run onward",
    caveats=["The address is normalised: https, lower-case host, tracking parameters and the part after # removed, no trailing slash. Parameters that change which page loads are kept.",
             "current_version_id points at the newest landing_page_versions row whose copy matches the last read (the copy can go back to an older version if the page is changed back)."],
    cols={"id": "Primary key (bigserial).", "url": "Normalised page address (unique).", "host": "Host name of the page, lower case.", "path": "Path and any kept query string of the page.",
          "first_seen_at": "When an ad was first found pointing at this page.", "last_seen_at": "When an ad was last found pointing at this page.",
          "last_fetched_at": "When the page was last read (successfully or not); NULL until the first read.", "last_status": "HTTP status of the last successful read; NULL before the first.",
          "last_error": "Short scrubbed error text when the last read failed; NULL after a good read.", "current_version_id": "landing_page_versions.id of the copy seen on the last good read.",
          "active": "False stops the page being read again; set by hand, nothing sets it."})

rel("public.landing_page_versions", group="meta", kind="table",
    purpose="The copy of a landing page as it was on a given read: title, description, ordered text blocks with heading level, buttons, prices, guarantee sentences, review snippets and image alt text.",
    grain="one row per (landing page, content hash): a new row exists only when the copy changed (id is the primary key)", source="etl/landing_pages.py: a plain public web request per page, parsed with the standard library (headless Chromium for pages built in the browser)",
    refresh="each run of python -m etl.landing_pages; a row is added only when the text hash changes", coverage="from the first read of each page",
    caveats=["content_hash covers the text only (title, description, text blocks, button text, image alt text), not image addresses or scripts, so changing a picture without changing its alt text is not a new version.",
             "Pages built in the browser have little text in the server HTML; those are read again with Chromium and rendered_in_browser is true. Text shown only after a click or inside an iframe is not captured.",
             "fetched_at is when this exact copy was first seen, not when it was last confirmed (landing_pages.last_fetched_at has that)."],
    cols={"id": "Primary key (bigserial).", "landing_page_id": "Page this copy belongs to (landing_pages.id, foreign key).", "content_hash": "sha256 of the page text (see caveats); unique per page.",
          "fetched_at": "When this copy was first seen.", "http_status": "HTTP status of the read that produced this copy.", "final_url": "Address after redirects.",
          "title": "Page title (the title tag, else the Open Graph title).", "meta_description": "Meta description (else the Open Graph description).",
          "rendered_in_browser": "True when the copy came from headless Chromium because the server HTML held almost no text.", "word_count": "Words in the page text.",
          "blocks": "Ordered text blocks as JSON: [{i, tag, level, text}], level is the heading level 1 to 6 or null, tag is the nearest label tag (h1..h6, p, li, blockquote, button, a, text).",
          "ctas": "Button and call to action text as JSON: [{text, href, tag}].", "prices": "Dollar amounts found in the text as JSON: [{price, context}].",
          "guarantees": "Sentences that mention a guarantee, refund, money back or risk free, as a JSON array of strings.", "reviews": "Review or testimonial snippets (quote blocks, star lines, structured data), as a JSON array of strings.",
          "images": "Image alt text as JSON: [{alt, src}].", "full_text": "All text blocks joined by new lines, for searching."})

rel("public.landing_page_ads", group="meta", kind="table",
    purpose="Which Meta ad sends people to which landing page, and how that link was found.",
    grain="one row per (ad, landing page) (ad_id + landing_page_id is the primary key)", source="etl/landing_pages.py: the Meta ad creative, the Page post an ad reuses, or the ad preview",
    refresh="each run of python -m etl.landing_pages (new ads with spend only; --refresh-ads looks at all again)", coverage="ads with spend in the 30 days before each run, from the first run onward",
    caveats=["An ad whose creative lists several links has one row per page. Its spend counts in full under each page in landing_page_top_ads, so spend must not be added across pages.",
             "ad_id is not a foreign key to meta_ads because ads can be deleted in Meta."],
    cols={"ad_id": "Meta ad id.", "landing_page_id": "landing_pages.id (foreign key).", "raw_url": "The link exactly as Meta returned it (with tracking parameters).",
          "source": "How the link was found: 'creative' (the ad's own creative), 'post' (the Page post the ad reuses), or 'preview' (the ad preview).",
          "first_seen_date": "Date the ad was first found with this link.", "last_seen_date": "Date the link was last confirmed."})

rel("public.landing_page_ad_checks", group="ops", kind="table",
    purpose="Ads that etl/landing_pages.py has already looked at, so an ad with no readable link is not asked about every night.",
    grain="one row per ad (ad_id is the primary key)", source="etl/landing_pages.py", refresh="each run of python -m etl.landing_pages", coverage="ads with spend in the window of the runs",
    caveats=["An ad with result no_link or error is looked at again 14 days after checked_at."],
    cols={"ad_id": "Meta ad id (primary key).", "checked_at": "When the ad was last looked at.", "result": "'linked' (a landing page was found), 'no_link' (none could be read) or 'error' (Meta returned an error).",
          "detail": "Short reason for no_link or error; NULL when linked."})

rel("public.landing_page_top_ads", group="meta", kind="view",
    purpose="The ads that drive each landing page, ranked by spend over the last 7 and the last 30 days, with Meta-reported purchases, revenue and ROAS (revenue divided by spend).",
    grain="one row per (landing page, window_days, ad): window_days is 7 or 30; only ads with spend in the window appear", source="public.landing_pages, public.landing_page_ads, public.ad_daily (Meta-reported) and public.meta_ads (name)",
    refresh="live view; the ad to page map moves with each run of python -m etl.landing_pages, spend with the nightly Meta pull", coverage="ads mapped to a page by the landing page job",
    caveats=["Purchases and revenue are what Meta reports (all customers), not Upstack new-customer numbers; join analytics.meta_ad_daily on ad_id when you need those.",
             "An ad that lists several links appears under each page with its full spend: do not add spend across pages.", "The window is the last N Pacific calendar days including today, which is a partial day."],
    cols={"landing_page_id": "landing_pages.id.", "url": "Normalised page address.", "window_days": "7 or 30.", "ad_id": "Meta ad id.", "ad_name": "Ad name from meta_ads; NULL when the ad is not there.",
          "campaign_id": "Campaign id from meta_ads; NULL when the ad is not there.", "spend": "Spend in the window, dollars (Meta).", "purchases": "Meta-reported purchases in the window.",
          "revenue": "Meta-reported purchase revenue in the window, dollars.", "roas": "revenue divided by spend, 3 decimals.", "rank_by_spend": "1 = biggest spender for this page and window."})
rel("public.brain_keys", group="ops", kind="table",
    purpose="Agent keys for the hosted brain endpoint (/mcp). With sign-in on: one row per user who made a key on the Connect an agent page (a reset replaces it). With the app open (AUTH_DISABLED): one row per key anyone made, with no user. A brain key opens /mcp and nothing else.",
    grain="one row per key (id is the primary key; key_hash is unique; username is unique and NULL for open-app keys)", source="the Connect an agent page (POST /api/brain/key in app/brain_mcp.py); last_used_at is touched by /mcp",
    refresh="on demand (a person makes a key; last_used_at moves at most once a minute while the key is in use)", coverage="from the day migration 220 was deployed; open-app keys from migration 221",
    caveats=["Only the sha256 hash of the key is stored. The key is shown once and cannot be recovered; a signed-in user resets it to get a new one (the old one stops working at once). Open-app keys cannot be reset, only made again.",
             "Separate from api_keys: a brain key never works on any /api/ route, and an api_keys key also works on /mcp.",
             "Open-app keys (created_by = 'open-app') are capped at 50 per day for the whole app and keep only a salted hash of the creator's IP, never the IP."],
    cols={"id": "Primary key (bigserial).", "username": "App login of the owner (a name from APP_USERS), unique: one key per user. NULL for open-app keys.", "key_hash": "sha256 hex digest of the full key (unique); the key is looked up by this hash.",
          "prefix": "First 8 characters of the key ('lvb_' plus 4), for display only.", "created_at": "When the key was made or last reset.", "last_used_at": "When the key last made a /mcp request (at most once a minute); NULL while unused.",
          "created_by": "How the key was made: 'signed-in' (a logged-in user, username is set) or 'open-app' (made while AUTH_DISABLED was on, username is NULL).",
          "creator_hash": "For open-app keys: HMAC-SHA256 of the creator's IP address under the app's secret key (first 32 hex characters); never the IP itself. NULL for signed-in keys."})

rel("public.brain_audit", group="ops", kind="table",
    purpose="Audit log of the hosted brain endpoint: one row per tool call (who, which tool, how long, how many rows). Never holds the question, the SQL or the result.",
    grain="one row per tool call (id is the primary key)", source="app/brain_mcp.py, written after every tools/call on /mcp",
    refresh="live (one row per call)", coverage="from the day migration 220 was deployed; no pruning",
    caveats=["actor is the login name for a signed-in session, 'brain:<username>' for a brain key, 'brain:open-app:<key prefix>' for a key made while the app was open, 'key:<name>' for an API key.",
             "row_count is NULL for tools that return no row list (describe, app_map)."],
    cols={"id": "Primary key (bigserial).", "at": "When the call finished.", "actor": "Who called: a login name, 'brain:<username>' (brain key), 'brain:open-app:<prefix>' (open-app brain key) or 'key:<name>' (API key).",
          "tool": "Tool name: catalog, describe, search, metric, freshness, sql, past_questions or app_map.", "duration_ms": "How long the tool ran, in milliseconds.",
          "row_count": "Rows (or hits) returned; NULL when the tool returns no list.", "ok": "True when the tool answered, false when it failed or the input was rejected."})
