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
    ("proj_engine", "Projection engine tables (finance, inventory, cohorts; NOT exposed in analytics)"),
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
             "(an attribution, not the actual) or the sheet's NC Rev - By Product (proj_feed_nc_product, sheet-fed, not in analytics).",
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
        "source": "meta_ad_daily | upstack_meta_attribution | google_campaign_daily | upstack_google_attribution | orders_daily | meta_reach (day grain) | meta_campaign_daily_history.",
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
    caveats=["The stage is not stored: it is derived at read time from the links, the timestamps and the status of the linked Creative Tracker rows (the tracker wins when it is further along).",
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
          "format_code": "Creative format chosen when assigning or later (creative_formats.code, foreign key); NULL when none was chosen. Set by a signed-in user only; a vendor sees it but cannot change it."})

rel("public.creative_formats", group="creative", kind="table",
    purpose="The creative formats a production assignment can be made in (for example Whiteboard explainer): a short list that Josh picks from when assigning a concept to a vendor. Production side only; not part of the concept taxonomy.",
    grain="one row per format (code is the primary key)", source="seeded by migration 204; edited by hand in the database (no page edits it); read by /api/priorities/assignments (app/priorities_assign.py)",
    refresh="on demand (a format is added or retired by hand)", coverage="six formats, F1 to F6, from the day the migration was deployed",
    caveats=["A format with active = false stays on the assignments that already use it but can no longer be chosen."],
    cols={"code": "Short code (primary key), F1 to F6.", "name": "What the format is, shown in the pick list.", "length": "Typical running time, as text (for example '45 to 90s'); NULL when not set.",
          "sort": "Order in the pick list (ascending).", "active": "True while the format can be chosen on a new assignment."})

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
    caveats=["Values equal upstack_orders_daily for the shared columns; tax and refunds exist only here. Not exposed in analytics (read orders_daily)."],
    cols={"day": "Pacific day.", "nc_rev": "New-customer revenue.", "rc_rev": "Returning-customer revenue.", "nc_orders": "New customers.", "total_rev": "Total revenue.",
          "total_orders": "All orders.", "tax": "Tax collected.", "refunds": "Refunds.", "loaded_at": "When loaded."})
rel("public.proj_feedx_orders", group="proj_feeds", kind="table", like="public.proj_feed_orders",
    purpose="Shadow twin of proj_feed_orders written by the feeds service from the Upstack API.", grain="one row per day", source="etl.feeds (Upstack /api/query)",
    refresh="feeds service 21:00 UTC, trailing window", coverage="trailing window only (about 14 days)", cols={})

rel("public.proj_feed_amazon", group="proj_feeds", kind="table",
    purpose="Amazon.com + B2B sales and Amazon Ads (deduped v3 > v2 > v1) by purchase day.",
    grain="one row per day", source=_SHEET.format("Amazon - Daily") + "; the direct feed reads ExpandFi merchant 1452", refresh=_FEED_REFRESH, coverage="2024-09-24 through yesterday",
    caveats=["Amazon ad spend must be de-duplicated across Amazon's v1 / v2 / v3 report generations (raw is about 1.3x high); this table already is.", "Not in analytics (Amazon is outside the Meta / Google marketing scope of this schema)."],
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
    caveats=["Not in analytics (a sheet-fed projection feed). Revenue = order total_price split across product lines by net line price; nc_orders = orders whose largest product line is the product."],
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
# public: projection engine (finance, inventory, cohorts). Documented for completeness; NOT exposed in analytics.
# =====================================================================================================================================

_ENG = dict(group="proj_engine", kind="table")
_FIN = "Projection engine (finance / planning); not exposed in analytics."

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
    refresh="on edit", coverage="74 rows", caveats=["Money (unit_cost) and vendor terms: finance. Not exposed."],
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

Railway Postgres holding Lonvera's marketing data: Meta ads (ad x day since 2026-05-06, campaign x day since 2024-12-31), Google Ads (campaign x day since 2024-03-25),
Upstack attribution (which orders each ad / campaign gets credit for), the business's actual new-customer revenue per day, and the Creative Tracker with its tags and hooks.
People and agents read it through the **read-only `analytics` schema** (role `analytics_reader`, SELECT on views only). Everything else is the app's own storage.

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
| is the data fresh, what failed | `analytics.data_coverage`, `analytics.etl_runs` |

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
* **Refund-adjusted revenue, margins, COGS, CAC by cohort, P&L**: finance lives in the projection engine tables (`proj_*`), deliberately outside `analytics`.
* **Email / SMS (Klaviyo), TikTok Shop and site analytics** are not in the database; Amazon sales and site traffic exist only as projection feeds (`proj_feed_amazon`, `proj_feed_traffic`), not exposed in `analytics`.
* **Creative files**: image bytes only in `public.creative_assets` (not exposed); the copy Meta actually runs only in `public.family_content` (not exposed).

## Excluded from the `analytics` schema (so far), and why

| Relation | Why it is not in `analytics` | Could be added? |
|---|---|---|
| `feed_oauth` | encrypted ExpandFi refresh token (a credential) | never |
| `schema_migrations` | migration bookkeeping | no |
| login / session | the app authenticates with environment-held users and signed cookies; there are no auth tables | n/a |
| `proj_scenarios`, `proj_series`, `proj_scenario_history`, `proj_audit` | Projections scenarios (financing, LOI, seller note live in `proj_scenarios.params`) and their audit trail | no: finance |
| `proj_actuals`, `proj_pnl_*`, `proj_upload_history` | QuickBooks actuals, P&L drivers, COGS, vendor retainers | no: finance |
| `proj_po`, `proj_fba_transfer`, `proj_vendor_*`, `proj_product_cost`, `proj_reorder_param`, `proj_stock_snapshot`, `proj_supply_history` | purchase orders, vendor terms and unit costs, stock | no: finance / supply costs |
| `proj_cohorts`, `proj_cohort_cum`, `proj_cohort_uploads`, `proj_curve_goal` | cohort revenue, CAC, payback, goal retention curve | Josh's call (cohort curves are marketing-adjacent but feed the finance model) |
| `proj_feed_orders`, `proj_feed_amazon`, `proj_feed_attribution`, `proj_feed_traffic`, `proj_feed_nc_product`, `proj_feed_anyclick`, `proj_feed_bids`, `proj_feed_meta_product`, `proj_feed_product_reach`, `proj_feed_platform`, `proj_units_*` | projection pacing feeds (sheet-fed today, shadow-fed by the `feeds` service); overlap with what `analytics` already exposes or sit in the middle of a cutover | Josh's call: `proj_feed_nc_product` (actual new-customer revenue by product) and `proj_feed_product_reach` are the most useful candidates |
| `proj_feedx_*`, `proj_feed_config / runs / attempts / verify* / levers`, `proj_refresh_runs`, `proj_pacing_*`, `proj_meta_spend_monthly` | shadow tables and feed plumbing | no |
| `creative_assets`, `family_content` | image bytes and the copy Meta runs | Josh's call |
| `ad_tags`, `ad_hooks`, `ad_metrics`, `tracker_rows`, `taxonomy_lines`, `line_ingredients`, `products` | the current tags, hooks, metrics and product of every tracker row are in `analytics.creative_tracker` | the raw tables are not needed |
| `ad_daily`, `ad_daily_upstack`, `meta_ads`, `meta_campaigns`, `meta_ad_lifetime`, `meta_family_map`, `google_daily`, `upstack_*`, `family_daily*`, `unmatched_meta*`, `ad_metrics_upstack_*` | raw and derived sources of the analytics views; the views are the supported surface | no |
| `etl_runs`, `app_meta` | `etl_runs` is exposed as a view; `app_meta` holds internal keys | no |

The `analytics.etl_runs` view exposes `stats` (job counters) and the first 500 characters of `error` (scrubbed of tokens).
`analytics.creative_tracker.asset_url` is a Google Drive link; opening it is governed by Drive permissions, not by this database.
"""

TABLE_REFERENCE_INTRO_MD = """\
## Reference

One section per relation, grouped as in the table below. Column types, nullability and keys come from the live schema; meaning, units and caveats from
`docs/schema/descriptions.py`. For views, nullability is not tracked by Postgres: read the text. 'like X' means the columns have the same meaning as in X.
"""


ANALYTICS_SCHEMA_COMMENT = (
    "Read-only views over Lonvera's marketing data (Meta, Google, Upstack attribution, actual orders, creative tracker). Start with data_coverage (what is loaded through which day) and read "
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
    purpose="Agent keys for the hosted brain endpoint (/mcp): one row per signed-in user who made a key on the Connect an agent page. A brain key opens /mcp and nothing else.",
    grain="one row per user (id is the primary key; username and key_hash are each unique)", source="the Connect an agent page (POST /api/brain/key in app/brain_mcp.py); last_used_at is touched by /mcp",
    refresh="on demand (a user makes or resets a key; last_used_at moves at most once a minute while the key is in use)", coverage="from the day migration 220 was deployed",
    caveats=["Only the sha256 hash of the key is stored. The key is shown once and cannot be recovered; reset it to get a new one (the old one stops working at once).",
             "Separate from api_keys: a brain key never works on any /api/ route, and an api_keys key also works on /mcp."],
    cols={"id": "Primary key (bigserial).", "username": "App login of the owner (a name from APP_USERS), unique: one key per user.", "key_hash": "sha256 hex digest of the full key (unique); the key is looked up by this hash.",
          "prefix": "First 8 characters of the key ('lvb_' plus 4), for display only.", "created_at": "When the key was made or last reset.", "last_used_at": "When the key last made a /mcp request (at most once a minute); NULL while unused."})

rel("public.brain_audit", group="ops", kind="table",
    purpose="Audit log of the hosted brain endpoint: one row per tool call (who, which tool, how long, how many rows). Never holds the question, the SQL or the result.",
    grain="one row per tool call (id is the primary key)", source="app/brain_mcp.py, written after every tools/call on /mcp",
    refresh="live (one row per call)", coverage="from the day migration 220 was deployed; no pruning",
    caveats=["actor is the login name for a signed-in session, 'brain:<username>' for a brain key, 'key:<name>' for an API key.",
             "row_count is NULL for tools that return no row list (describe, app_map)."],
    cols={"id": "Primary key (bigserial).", "at": "When the call finished.", "actor": "Who called: a login name, 'brain:<username>' (brain key) or 'key:<name>' (API key).",
          "tool": "Tool name: catalog, describe, search, metric, freshness, sql, past_questions or app_map.", "duration_ms": "How long the tool ran, in milliseconds.",
          "row_count": "Rows (or hits) returned; NULL when the tool returns no list.", "ok": "True when the tool answered, false when it failed or the input was rejected."})
