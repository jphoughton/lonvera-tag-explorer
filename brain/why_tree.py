"""The metric tree behind the `why` tool, as data. Derived from Josh's own question history (docs/questions/ ledgers), the 2026-10-26 free-fall why-chain (SYNTHESIS.md, checkout_split_REPORT.md)
and the diagnose-performance skill, not from a textbook.

Arrows in the tree mean 'of which' (an accounting identity), never 'because'. Causes are found by the drill loop in brain/why.py: ranked contributors, dated change events, and a control for every candidate.

Every SQL text below is read only, runs against analytics.* views only, and is parameterised by {periods} (a VALUES list of (label, start, end) built from validated dates) and by a few validated
literals ({prod}, {key}, ...). Nothing the caller types is pasted into SQL except validated dates and whitelisted slugs.
"""
from __future__ import annotations

from .why_questions import PAST_QUESTIONS

PERIODS = "periods(p, s, e) as (values {periods})"

# ------------------------------------------------------------------ scalar families: one aggregated query each, covering every period of every grain at once
FAMILIES: dict[str, dict] = {
    "orders": {"needs": [], "measures": ["o_days", "revenue", "nc_rev", "rep_rev", "nc", "orders", "rep_orders", "base_nc"], "sql": """
with """ + PERIODS + """
select p.p, count(o.day) o_days, coalesce(sum(o.total_revenue), 0) revenue, coalesce(sum(o.new_customer_revenue), 0) nc_rev, coalesce(sum(o.returning_customer_revenue), 0) rep_rev,
       coalesce(sum(o.new_customers), 0) nc, coalesce(sum(o.orders), 0) orders, coalesce(sum(o.orders - o.new_customers), 0) rep_orders,
       (select coalesce(sum(b.new_customers), 0) from analytics.orders_daily b where b.day between p.s - 180 and p.s - 1) base_nc
from periods p left join analytics.orders_daily o on o.day between p.s and p.e
group by p.p, p.s"""},
    "spend": {"needs": [], "measures": ["meta_days", "google_days", "spend", "meta_spend", "google_spend", "meta_own_rev", "meta_up_nc_rev", "google_up_nc_rev", "google_own_rev"], "sql": """
with """ + PERIODS + """
select p.p, count(distinct d.day) filter (where d.platform = 'meta') meta_days, count(distinct d.day) filter (where d.platform = 'google') google_days,
       coalesce(sum(d.spend), 0) spend, coalesce(sum(d.spend) filter (where d.platform = 'meta'), 0) meta_spend, coalesce(sum(d.spend) filter (where d.platform = 'google'), 0) google_spend,
       coalesce(sum(d.own_revenue) filter (where d.platform = 'meta'), 0) meta_own_rev, coalesce(sum(d.upstack_new_customer_revenue) filter (where d.platform = 'meta'), 0) meta_up_nc_rev,
       coalesce(sum(d.upstack_new_customer_revenue) filter (where d.platform = 'google'), 0) google_up_nc_rev, coalesce(sum(d.own_revenue) filter (where d.platform = 'google'), 0) google_own_rev
from periods p left join analytics.platform_daily d on d.day between p.s and p.e
group by p.p"""},
    "reach": {"needs": [], "measures": ["r_days", "impressions", "clicks", "link_clicks", "reach_daily"], "sql": """
with """ + PERIODS + """
select p.p, count(r.period_key) r_days, coalesce(sum(r.impressions), 0) impressions, coalesce(sum(r.unique_outbound_clicks), 0) clicks, coalesce(sum(r.link_clicks), 0) link_clicks,
       coalesce(sum(r.reach), 0) reach_daily
from periods p left join analytics.meta_reach r on r.grain = 'day' and r.period_start between p.s and p.e
group by p.p"""},
    # scoped (a product and / or a single platform): Upstack-attributed numbers from the ad views, not the actual orders
    "sc_meta": {"needs": [], "measures": ["meta_days", "spend", "meta_spend", "impressions", "clicks", "nc", "nc_rev", "meta_own_rev", "meta_up_nc_rev"], "sql": """
with """ + PERIODS + """
select p.p, count(distinct d.day) meta_days, coalesce(sum(d.spend), 0) spend, coalesce(sum(d.spend), 0) meta_spend, coalesce(sum(d.impressions), 0) impressions, coalesce(sum(d.link_clicks), 0) clicks,
       coalesce(sum(d.upstack_new_customers), 0) nc, coalesce(sum(d.upstack_new_customer_revenue), 0) nc_rev, coalesce(sum(d.meta_revenue), 0) meta_own_rev, coalesce(sum(d.upstack_new_customer_revenue), 0) meta_up_nc_rev
from periods p left join analytics.meta_ad_daily d on d.day between p.s and p.e {prod}
group by p.p"""},
    "sc_google": {"needs": [], "measures": ["google_days", "spend", "meta_spend", "impressions", "clicks", "nc", "nc_rev", "meta_own_rev", "meta_up_nc_rev"], "sql": """
with """ + PERIODS + """
select p.p, count(distinct g.day) google_days, coalesce(sum(g.spend), 0) spend, coalesce(sum(g.spend), 0) meta_spend, coalesce(sum(g.impressions), 0) impressions, coalesce(sum(g.clicks), 0) clicks,
       coalesce(sum(g.upstack_new_customers), 0) nc, coalesce(sum(g.upstack_new_customer_revenue), 0) nc_rev, coalesce(sum(g.google_revenue), 0) meta_own_rev, coalesce(sum(g.upstack_new_customer_revenue), 0) meta_up_nc_rev
from periods p left join analytics.google_campaign_daily g on g.day between p.s and p.e
group by p.p"""},
    # optional views (not exposed yet on every build): the nodes say so until the view exists
    "klaviyo": {"needs": ["klaviyo_daily"], "measures": ["k_flow_rev", "k_flow_deliv", "k_flow_orders", "k_camp_rev", "k_camp_deliv", "k_camp_orders", "k_camp_email_orders", "k_bill_deliv", "k_bill_unsubs",
                                                         "k_fw_rev", "k_fw_deliv"], "sql": """
with """ + PERIODS + """
select p.p, coalesce(sum(k.revenue) filter (where k.source = 'flow'), 0) k_flow_rev, coalesce(sum(k.delivered) filter (where k.source = 'flow'), 0) k_flow_deliv,
       coalesce(sum(k.orders) filter (where k.source = 'flow'), 0) k_flow_orders, coalesce(sum(k.revenue) filter (where k.source = 'campaign'), 0) k_camp_rev,
       coalesce(sum(k.delivered) filter (where k.source = 'campaign'), 0) k_camp_deliv, coalesce(sum(k.orders) filter (where k.source = 'campaign'), 0) k_camp_orders,
       coalesce(sum(k.orders) filter (where k.source = 'campaign' and k.channel = 'email'), 0) k_camp_email_orders,
       coalesce(sum(k.delivered) filter (where k.source = 'flow' and k.parent_name ~* 'billing|reminder|renewal|upcoming'), 0) k_bill_deliv,
       coalesce(sum(k.unsubscribes) filter (where k.source = 'flow' and k.parent_name ~* 'billing|reminder|renewal|upcoming'), 0) k_bill_unsubs,
       coalesce(sum(k.revenue) filter (where k.source = 'flow' and k.parent_name ~* 'failed|decline|payment|winback|win-back|win back|cancel'), 0) k_fw_rev,
       coalesce(sum(k.delivered) filter (where k.source = 'flow' and k.parent_name ~* 'failed|decline|payment|winback|win-back|win back|cancel'), 0) k_fw_deliv
from periods p left join analytics.klaviyo_daily k on k.day between p.s and p.e
group by p.p"""},
    "shopify": {"needs": ["shopify_orders", "shopify_order_lines"], "measures": ["so_orders", "so_rev", "so_units", "so_disc"], "sql": """
with """ + PERIODS + """
select p.p, count(o.order_id) so_orders, coalesce(sum(o.total_price), 0) so_rev, coalesce(sum(l.units), 0) so_units, coalesce(sum(o.total_discounts), 0) so_disc
from periods p left join analytics.shopify_orders o on o.order_date between p.s and p.e and o.is_new_customer and not o.is_cancelled and not o.is_test and not o.is_trybe_gift
left join (select order_id, sum(quantity) units from analytics.shopify_order_lines group by 1) l on l.order_id = o.order_id
group by p.p"""},
}

# ------------------------------------------------------------------ mix groups: one query per group returns every measure per (period, value); rates and decompositions reuse it
_META_MEASURES = """coalesce(sum(d.spend), 0) spend, coalesce(sum(d.impressions), 0) impressions, coalesce(sum(d.link_clicks), 0) clicks, coalesce(sum(d.meta_purchases), 0) meta_purchases,
       coalesce(sum(d.meta_revenue), 0) meta_revenue, coalesce(sum(d.upstack_new_customers), 0) nc, coalesce(sum(d.upstack_new_customer_revenue), 0) nc_rev,
       count(distinct d.ad_id) filter (where d.spend > 0) live_ads"""
_META_FROM = """from periods p join analytics.meta_ad_daily d on d.day between p.s and p.e
left join analytics.meta_ads a on a.ad_id = d.ad_id
left join analytics.creative_tracker t on t.family_key = a.tracker_family_key and t.is_first_row
{extra}where true {prod}"""
_TAG = lambda col: f"coalesce(nullif(t.{col}, ''), '(untagged)')"  # noqa: E731
MIX_GROUPS: dict[str, dict] = {
    "product": {"source": "meta", "key": "coalesce(d.product_slug, 'unassigned')", "label": "coalesce(d.product_slug, 'unassigned')", "extra": "", "needs": [], "what": "product the app assigns to the ad (meta_ads.product_slug)"},
    "stage": {"source": "meta", "key": "coalesce(d.stage, 'unknown')", "label": "coalesce(d.stage, 'unknown')", "extra": "", "needs": [], "what": "testing vs scaling (campaign name contains 'test')"},
    "family": {"source": "meta", "key": "coalesce(a.family_key, lower(d.ad_name), d.ad_id)", "label": "max(coalesce(a.family_name, d.ad_name, d.ad_id))", "extra": "", "needs": [], "what": "ad family (copies of one ad together)"},
    "cluster": {"source": "meta", "key": _TAG("cluster"), "label": _TAG("cluster"), "extra": "", "needs": [], "what": "market cluster tag (current line-mapped or tracker tag of the ad's family)"},
    "icp": {"source": "meta", "key": _TAG("icp"), "label": _TAG("icp"), "extra": "", "needs": [], "what": "ICP (persona) tag"},
    "pain_point": {"source": "meta", "key": _TAG("pain_point"), "label": _TAG("pain_point"), "extra": "", "needs": [], "what": "pain point tag"},
    "emotional_trigger": {"source": "meta", "key": _TAG("emotional_trigger"), "label": _TAG("emotional_trigger"), "extra": "", "needs": [], "what": "emotional trigger tag"},
    "ad_type": {"source": "meta", "key": _TAG("ad_type"), "label": _TAG("ad_type"), "extra": "", "needs": [], "what": "creative type (image / video) from the tracker"},
    "style": {"source": "meta", "key": _TAG("style"), "label": _TAG("style"), "extra": "", "needs": [], "what": "creative style from the tracker"},
    "vendor": {"source": "meta", "key": _TAG("vendor"), "label": _TAG("vendor"), "extra": "", "needs": [], "what": "creative vendor from the tracker"},
    "hook": {"source": "meta", "key": _TAG("hook"), "label": _TAG("hook"), "extra": "", "needs": [], "what": "hook text (first line of the ad)"},
    "landing_page": {"source": "meta", "key": "coalesce(lp.url, '(no page mapped)')", "label": "coalesce(lp.url, '(no page mapped)')", "needs": ["landing_page_ads", "landing_pages"], "what": "landing page the ad sends to (landing_page_ads)",
                     "extra": "left join (select ad_id, min(landing_page_id) pid from analytics.landing_page_ads group by 1) lpa on lpa.ad_id = d.ad_id\nleft join analytics.landing_pages lp on lp.id = lpa.pid\n"},
    "g_type": {"source": "google", "key": "coalesce(g.campaign_type, 'unknown')", "label": "coalesce(g.campaign_type, 'unknown')", "extra": "", "needs": [], "what": "Google campaign type"},
    "g_campaign": {"source": "google", "key": "coalesce(g.campaign_key, g.campaign_name)", "label": "max(g.campaign_name)", "extra": "", "needs": [], "what": "Google campaign"},
}
BREAKDOWNS = ["audience_segment", "age", "gender", "age_gender", "country", "region", "publisher_platform", "platform_position", "device_platform", "hourly"]
for _b in BREAKDOWNS:
    MIX_GROUPS["bd_" + _b] = {"source": "breakdown", "breakdown": _b, "key": "coalesce(g.value, 'unknown')", "label": "coalesce(g.value, 'unknown')", "extra": "", "needs": ["meta_breakdown_daily"],
                              "what": f"Meta's own breakdown by {_b.replace('_', ' ')} (analytics.meta_breakdown_daily, adset grain, Meta-reported only)"}
_B_MEASURES = """coalesce(sum(g.spend), 0) spend, coalesce(sum(g.impressions), 0) impressions, coalesce(sum(g.unique_outbound_clicks), 0) clicks, coalesce(sum(g.purchases), 0) meta_purchases,
       coalesce(sum(g.purchase_value), 0) meta_revenue, 0 nc, 0 nc_rev, count(distinct g.adset_id) filter (where g.spend > 0) live_ads"""
_B_FROM = "from periods p join analytics.meta_breakdown_daily g on g.day between p.s and p.e\nwhere g.breakdown = '{b}'"
_GROUP_SQL = """
with {periods_cte}, g as (
  select p.p, {key} k, {label} lbl, {measures}
  {frm}
  group by p.p, 2),
top as (select k from g group by k order by sum(spend) desc limit {top_n}){having}
select p, k, lbl, spend, impressions, clicks, meta_purchases, meta_revenue, nc, nc_rev, live_ads from g where k in (select k from top) {only}
union all
select p, '~other', 'all other values', sum(spend), sum(impressions), sum(clicks), sum(meta_purchases), sum(meta_revenue), sum(nc), sum(nc_rev), sum(live_ads) from g where k not in (select k from top) {only_o} group by p"""
_G_MEASURES = """coalesce(sum(g.spend), 0) spend, coalesce(sum(g.impressions), 0) impressions, coalesce(sum(g.clicks), 0) clicks, coalesce(sum(g.google_purchases), 0) meta_purchases,
       coalesce(sum(g.google_revenue), 0) meta_revenue, coalesce(sum(g.upstack_new_customers), 0) nc, coalesce(sum(g.upstack_new_customer_revenue), 0) nc_rev, count(distinct g.campaign_key) filter (where g.spend > 0) live_ads"""
_G_FROM = "from periods p join analytics.google_campaign_daily g on g.day between p.s and p.e\nwhere true"


def group_sql(group: str, top_n: int = 40, keys: list[str] | None = None) -> str:
    """Template with {periods} and {prod} left open. `keys`, when given, restricts the result to those values (values come from the database, escaped here)."""
    g = MIX_GROUPS[group]
    meta = g["source"] == "meta"
    frm = _META_FROM.replace("{extra}", g["extra"]) if meta else (_B_FROM.replace("{b}", g["breakdown"]) if g["source"] == "breakdown" else _G_FROM)
    if keys:
        lit = ", ".join("'" + str(k).replace("'", "''").replace("\x00", "") + "'" for k in keys)
        only, only_o, having = f"and k in ({lit})", f"and k in ({lit})", ""
        top_n = 100000
    else:
        only = only_o = having = ""
    return _GROUP_SQL.format(periods_cte=PERIODS, key=g["key"], label=g["label"], measures=_META_MEASURES if meta else (_B_MEASURES if g["source"] == "breakdown" else _G_MEASURES), frm=frm, top_n=top_n, having=having, only=only, only_o=only_o)


# ------------------------------------------------------------------ change sources (dated events); each needs the views named
CHANGE_SQL: dict[str, dict] = {
    "ad_launches": {"needs": [], "where": "analytics.meta_ads + meta_ad_daily", "sql": """
with fam as (select a.family_key fk, max(a.family_name) fname, min(a.first_day) fd, max(a.product_slug) product, max(coalesce(nullif(t.cluster, ''), '(untagged)')) cluster, max(coalesce(nullif(t.icp, ''), '(untagged)')) icp,
                    max(coalesce(nullif(t.pain_point, ''), '(untagged)')) pain_point, count(*) ads
             from analytics.meta_ads a left join analytics.creative_tracker t on t.family_key = a.tracker_family_key and t.is_first_row
             where a.first_day is not null group by a.family_key),
     sp as (select coalesce(a.family_key, lower(d.ad_name)) fk, sum(d.spend) spend from analytics.meta_ad_daily d left join analytics.meta_ads a on a.ad_id = d.ad_id
            where d.day between date '{hstart}' and date '{end}' group by 1)
select fam.fk, fam.fname, fam.fd, fam.product, fam.cluster, fam.icp, fam.pain_point, fam.ads, coalesce(sp.spend, 0) spend
from fam left join sp on sp.fk = fam.fk where fam.fd between date '{hstart}' and date '{end}' order by coalesce(sp.spend, 0) desc limit 300"""},
    "adset_moves": {"needs": [], "where": "analytics.meta_adset_daily", "sql": """
with a as (select adset_id, max(adset_name) nm, max(campaign_name) cn, max(stage) stage, min(day) fd, max(day) ld, coalesce(sum(spend), 0) spend_h,
                  coalesce(sum(spend) filter (where day between date '{start}' and date '{end}'), 0) spend_cur, coalesce(sum(spend) filter (where day between date '{cstart}' and date '{cend}'), 0) spend_cmp
           from analytics.meta_adset_daily where day between date '{hstart}' and date '{end}' or day between date '{cstart}' and date '{cend}' group by adset_id)
select adset_id, nm, cn, stage, fd, ld, spend_cur, spend_cmp,
       case when fd between date '{hstart}' and date '{end}' and fd > date '{cstart}' then 'launched' when ld < date '{end}' - 2 and spend_cmp > 0 then 'stopped'
            when spend_cmp = 0 and spend_cur > 0 then 'restarted' when spend_cmp >= 100 and spend_cur <= 0.5 * spend_cmp then 'down' when spend_cmp >= 100 and spend_cur >= 1.5 * spend_cmp then 'up' else 'steady' end kind
from a where spend_cur + spend_cmp >= 100 order by abs(spend_cur - spend_cmp) desc limit 300"""},
    "feed_breaks": {"needs": [], "where": "analytics.etl_runs + data_coverage", "sql": """
select started_at::date as day, job, status, left(coalesce(error, ''), 120) error from analytics.etl_runs where status = 'failed' and started_at::date between date '{hstart}' and date '{end}' order by started_at desc limit 50"""},
    "landing_page_versions": {"needs": ["landing_pages", "landing_page_versions"], "where": "analytics.landing_page_versions", "sql": """
select p.id page_id, p.url, v.fetched_at::date as day, v.title, v.word_count, (select count(*) from analytics.landing_page_versions x where x.landing_page_id = p.id) versions
from analytics.landing_page_versions v join analytics.landing_pages p on p.id = v.landing_page_id
where v.fetched_at::date between date '{hstart}' and date '{end}' order by v.fetched_at desc limit 100"""},
    "klaviyo_flow_changes": {"needs": ["klaviyo_flows"], "where": "analytics.klaviyo_flows", "sql": """
select id, name, status, created_at::date created_day, updated_at::date updated_day from analytics.klaviyo_flows
where created_at::date between date '{hstart}' and date '{end}' or updated_at::date between date '{hstart}' and date '{end}' order by updated_at desc limit 100"""},
}
ROUTING_SQL = """
with {periods_cte}, x as (
  select p.p, {d1} dk, {d2} pg, coalesce(sum(d.spend), 0) spend, coalesce(sum(d.link_clicks), 0) clicks, coalesce(sum(d.upstack_new_customers), 0) nc, coalesce(sum(d.upstack_new_customer_revenue), 0) nc_rev
  from periods p join analytics.meta_ad_daily d on d.day between p.s and p.e
  left join analytics.meta_ads a on a.ad_id = d.ad_id
  left join analytics.creative_tracker t on t.family_key = a.tracker_family_key and t.is_first_row
  {extra}where p.p in ('cur', 'cmp') {prod}
  group by 1, 2, 3),
rk as (select x.*, row_number() over (partition by p, dk order by spend desc) rn from x),
topdim as (select dk from x group by dk order by sum(spend) desc limit 12)
select p, dk, case when rn <= 8 then pg else '~other' end pg, sum(spend) spend, sum(clicks) clicks, sum(nc) nc, sum(nc_rev) nc_rev from rk where dk in (select dk from topdim) group by 1, 2, 3"""
LP_ROUTE_EXTRA = ("left join (select ad_id, min(landing_page_id) pid from analytics.landing_page_ads group by 1) lpa on lpa.ad_id = d.ad_id\n"
                  "left join analytics.landing_pages lp on lp.id = lpa.pid\n")
D2 = {"page": ("coalesce(lp.url, '(no page mapped)')", LP_ROUTE_EXTRA, ["landing_page_ads", "landing_pages"], "landing page"),
      "stage": ("coalesce(d.stage, 'unknown')", "", [], "testing vs scaling"),
      "campaign": ("coalesce(d.campaign_name, 'unknown')", "", [], "campaign")}
ROUTING_PAIRS = [("cluster", "page"), ("icp", "page"), ("product", "page"), ("family", "page"), ("cluster", "stage"), ("cluster", "campaign")]
ROUTING_FIRST_USE_SQL = """
select a.family_key fk, max(a.family_name) fname, min(d.day) first_day_on_dest, min(a.first_day) family_first_day, coalesce(sum(d.spend), 0) spend
from analytics.meta_ad_daily d join analytics.meta_ads a on a.ad_id = d.ad_id
left join analytics.creative_tracker t on t.family_key = a.tracker_family_key and t.is_first_row
{extra}where {d1} = '{v1}' and {d2} = '{v2}' and d.day between date '{hstart}' and date '{end}' {prod}
group by a.family_key order by 5 desc limit 5"""
BD_AD_SQL = """
with {periods_cte}, x as (
  select p.p, g.breakdown b, g.value v, (a.family_key = '{fk}') inf, coalesce(sum(g.spend), 0) spend
  from periods p join analytics.meta_breakdown_daily g on g.day between p.s and p.e
  join analytics.meta_ads a on a.ad_id = g.ad_id
  where p.p in ('cur', 'cmp') and g.breakdown in ('audience_segment', 'age_gender', 'platform_position') and g.ad_id is not null
  group by 1, 2, 3, 4)
select p, b, v, inf, spend from x"""
ROUTING_BD_SQL = """
with {periods_cte}, x as (
  select p.p, coalesce(g.value, 'unknown') dk, coalesce(g.campaign_id, '(none)') pg, coalesce(sum(g.spend), 0) spend, coalesce(sum(g.unique_outbound_clicks), 0) clicks, coalesce(sum(g.purchases), 0) nc,
         coalesce(sum(g.purchase_value), 0) nc_rev
  from periods p join analytics.meta_breakdown_daily g on g.day between p.s and p.e
  where p.p in ('cur', 'cmp') and g.breakdown = '{b}' and g.campaign_id is not null group by 1, 2, 3),
rk as (select x.*, row_number() over (partition by p, dk order by spend desc) rn from x),
topdim as (select dk from x group by dk order by sum(spend) desc limit 12)
select p, dk, case when rn <= 8 then pg else '~other' end pg, sum(spend) spend, sum(clicks) clicks, sum(nc) nc, sum(nc_rev) nc_rev from rk where dk in (select dk from topdim) group by 1, 2, 3"""
ROUTING_BD_PAIRS = [("audience_segment", "campaign"), ("age_gender", "campaign")]
ROUTING_BD_GAPS = [("audience_segment", "landing page"), ("audience_segment", "ad family")]
LP_FOR_ADS_SQL = """
select m.ad_id, a.family_key, p.url, p.last_fetched_at::date last_read, v.fetched_at::date copy_since, v.title, v.word_count,
       (select count(*) from analytics.landing_page_versions x where x.landing_page_id = p.id) versions
from analytics.landing_page_ads m join analytics.meta_ads a on a.ad_id = m.ad_id join analytics.landing_pages p on p.id = m.landing_page_id
left join analytics.landing_page_versions v on v.id = p.current_version_id where a.family_key in ({keys}) limit 50"""
LEAD_AD_SQL = """
select a.family_key, max(a.family_name) fname, min(a.first_day) first_day, min(a.created_date) created, count(*) ads, max(a.product_slug) product, max(a.stage) stage, max(a.adset_name) adset_name, max(a.campaign_name) campaign_name,
       max(substring(coalesce(array_to_string(t.primary_text, ' '), '') || ' ' || coalesce(t.ad_description, '') || ' ' || coalesce(t.description, '') from 'https?://[^[:space:])"]+')) link_in_ad_copy, max(t.hook) hook
from analytics.meta_ads a left join analytics.creative_tracker t on t.family_key = a.tracker_family_key and t.is_first_row
where a.family_key in ({keys}) group by a.family_key limit 50"""
COVERAGE_SQL = "select source, first_day, last_day from analytics.data_coverage"
VIEWS_SQL = "select table_name from information_schema.tables where table_schema = 'analytics'"

# ------------------------------------------------------------------ the nodes
M = lambda **k: k  # noqa: E731


def _kc(date, claim, status, confidence, direction=None, between=None, source="", note=""):
    return {"date": date, "claim": claim, "status": status, "confidence": confidence, "direction": direction, "between": between, "source": source, "note": note}


SYNTH = "lonvera_whychain_2026-10-26/SYNTHESIS.md"
NODES: list[dict] = []


def node(**k):
    k.setdefault("kind", "scalar")
    k.setdefault("children", [])
    k.setdefault("known_causes", [])
    k.setdefault("comparisons", ["prior_period", "last_month", "last_year", "custom"])
    k.setdefault("account_only", False)
    k.setdefault("pq", k["id"])
    NODES.append(k)
    return k


def _part(i, sign=1):
    return {"id": i, "role": "part", "sign": sign}


def _info(i):
    return {"id": i, "role": "info", "sign": 0}


node(id="revenue", label="Revenue (new + repeat, gross)", formula="total_revenue = new_customer_revenue + returning_customer_revenue (gross: tax in, refunds not out)", unit="usd", num="revenue", link="add", account_only=True,
     children=[_part("nc_revenue"), _part("repeat_revenue"), _part("amazon_revenue")], mat=M(rel=0.06, abs=150, min_den=None),
     known_causes=[_kc("2026-10-26", "New customers per week fell 74% (Jul 12 to Oct 18); spend explains about three quarters of it", "verified", "supported by control", "down", ["2026-07-12", None], SYNTH),
                   _kc("2026-10-26", "Repeat revenue fell $237K to $158K a week, only partly cohort lag", "verified", "consistent with", "down", ["2026-09-14", None], SYNTH)],
     settles_with="the child nodes: which part moved, then that part's own children")
node(id="amazon_revenue", label="Amazon revenue", kind="gap", formula="Amazon sales (not part of orders_daily)", unit="usd",
     data_gap={"why": "Amazon sales are not in the analytics schema (they exist only as the projection feed proj_feed_amazon, sheet / ExpandFi fed).", "where": "ExpandFi merchant 1452 `sales` table (Amazon.com + B2B); sheet tabs 'Amazon Daily Units' / the P&L row 19",
               "follow_up": "expose proj_feed_amazon as analytics.amazon_daily"})
node(id="nc_revenue", label="New-customer revenue", formula="nc_revenue = total spend x NC ROAS (new-customer revenue = first orders only)", unit="usd", num="nc_rev", link="mult", children=[_part("spend"), _part("nc_roas"), _info("nc_campaign_email_share")],
     mat=M(rel=0.06, abs=100, min_den=None), known_causes=[_kc("2026-10-26", "Spend -63% explains ~3/4 of the new-customer fall; yield per dollar the rest", "verified", "supported by control", "down", ["2026-07-12", "2026-10-18"], SYNTH)],
     settles_with="spend and NC ROAS children; for a cause, the Meta mix / bid nodes under them")
node(id="repeat_revenue", label="Repeat revenue (returning customers, rebills included)", formula="returning_customer_revenue = repeat orders x revenue per repeat order", unit="usd", num="rep_rev", link="mult", account_only=True,
     children=[_part("repeat_orders"), _part("repeat_aov")], mat=M(rel=0.06, abs=80, min_den=None),
     known_causes=[_kc("2026-10-26", "Skio base shrank $42.5K a week: renewals per renewal due 0.84 -> 0.70, cancels per due 0.41 -> 0.49, both starting the week the billing-reminder flows scaled (Sep 14-15)", "open", "consistent with", "down", ["2026-09-14", None], SYNTH,
                        "not randomised; settle with a fixed-assignment 50/50 holdout on the reminder email"),
                   _kc("2026-10-23", "'Repeat is behind plan mostly timing'", "refuted", "refuted", "down", ["2026-10-01", "2026-10-31"], "causal-claims-need-control.md", "same-structure cohorts (Aug vs Sep M1) showed the gap without a timing difference"),
                   _kc("2026-10-17", "PayPal rebills failed Oct 12-13 and billed 3 days late", "verified", "consistent with", "down", ["2026-10-12", "2026-10-16"], "ledger_marketing.md #23 / #60"),
                   _kc("2026-10-26", "About half of the 35-day repeat drop is the shift to 3-packs, which rebill later", "verified", "consistent with", "down", ["2026-03-01", None], SYNTH)],
     settles_with="repeat orders per base customer against the same cohort age (cohort views), a billing-reminder holdout")
node(id="repeat_orders", label="Repeat orders", formula="repeat orders = orders - new customers", unit="count", num="rep_orders", link="info", account_only=True,
     children=[_info("repeat_rate"), _info("klaviyo_flow_revenue"), _info("klaviyo_campaign_revenue"), _info("klaviyo_billing_reminders"), _info("klaviyo_failed_winback"), _info("renewals_per_due"), _info("repeat_cohort_like_for_like")],
     mat=M(rel=0.06, abs=3, min_den=None))
node(id="repeat_rate", label="Repeat orders per recent new customer (proxy)", formula="repeat orders / new customers acquired in the 180 days before the window", unit="ratio", num="rep_orders", den="base_nc", link="info", account_only=True,
     mat=M(rel=0.08, abs=0.01, min_den=500), settles_with="a same-age cohort comparison (the proxy mixes cohort ages); cohort views are not in analytics yet")
node(id="repeat_aov", label="Revenue per repeat order", formula="returning revenue / repeat orders", unit="usd", num="rep_rev", den="rep_orders", link="info", account_only=True, mat=M(rel=0.05, abs=2, min_den=30),
     known_causes=[_kc("2026-10-22", "Shipping insurance on rebills", "open", "untested", None, None, "ledger_marketing.md #88")])
node(id="renewals_per_due", label="Renewals per rebill due; cancels per due", kind="gap", formula="renewals / rebills due (a rate, not a count)", unit="ratio",
     data_gap={"why": "Rebills due, renewals and cancels per due are subscription-platform data, not in the analytics schema.", "where": "Skio and CheckoutChamp (recurring orders table; Skio dashboard); the Klaviyo billing-reminder flows for timing",
               "follow_up": "same-cohort renewal-per-due view (feat/brain-views cohorts) and a billing-reminder 50/50 holdout"},
     known_causes=[_kc("2026-10-26", "Renewals per due 0.84 -> 0.70 from Sep 14-15, when the billing-reminder flows scaled", "open", "consistent with", "down", ["2026-09-14", None], SYNTH, "settle with a randomised holdout on the reminder email")])
node(id="repeat_cohort_like_for_like", label="Repeat revenue vs the same cohort at the same age", kind="gap", formula="cohort M1..Mn per customer vs the prior cohort at the same age and day of month", unit="ratio",
     data_gap={"why": "Cohort revenue curves are in the projection tables (proj_cohorts), not in analytics.", "where": "Upstack / Shopify cohort export; the sheet 'Cohort Data - Unified'; app Projection page", "follow_up": "analytics cohort view (feat/brain-views)"})
node(id="klaviyo_flow_revenue", label="Klaviyo flow revenue per delivered message", kind="scalar", formula="flow revenue / flow messages delivered", unit="usd", num="k_flow_rev", den="k_flow_deliv", link="info", account_only=True,
     needs_views=["klaviyo_daily"], gap_text="Klaviyo not in the database yet", gap_where="Klaviyo API (Placed Order - Upstackified metric); Klaviyo flow reports", mat=M(rel=0.1, abs=0.01, min_den=1000))
node(id="klaviyo_campaign_revenue", label="Klaviyo campaign revenue per delivered message", formula="campaign revenue / campaign messages delivered", unit="usd", num="k_camp_rev", den="k_camp_deliv", link="info", account_only=True,
     needs_views=["klaviyo_daily"], gap_text="Klaviyo not in the database yet", gap_where="Klaviyo campaign reports", mat=M(rel=0.1, abs=0.01, min_den=1000))
node(id="klaviyo_billing_reminders", label="Billing-reminder flow messages delivered", formula="delivered messages of flows named billing / reminder / renewal / upcoming", unit="count", num="k_bill_deliv", link="info", account_only=True,
     needs_views=["klaviyo_daily", "klaviyo_flows"], gap_text="Klaviyo not in the database yet", gap_where="Klaviyo flows (created / updated dates via the API)", mat=M(rel=0.15, abs=50, min_den=None),
     known_causes=[_kc("2026-10-26", "Billing-reminder flows scaled Sep 14-15 (old flow ~9x, new CheckoutChamp flow launched); renewals per due fell the same week", "open", "consistent with", None, ["2026-09-14", None], SYNTH,
                       "fixed-assignment 50/50 holdout")])
node(id="klaviyo_failed_winback", label="Failed-payment and winback flow revenue per delivered message", formula="revenue / delivered for flows named failed / decline / payment / winback / cancel", unit="usd", num="k_fw_rev", den="k_fw_deliv", link="info", account_only=True,
     needs_views=["klaviyo_daily"], gap_text="Klaviyo not in the database yet", gap_where="Klaviyo flows (the 2026-10-13 audit: no live failed-payment flow, none on CKC Cancelled)", mat=M(rel=0.15, abs=0.01, min_den=500))
node(id="nc_campaign_email_share", label="Campaign-email orders per new customer", formula="Klaviyo campaign-email attributed orders / actual new customers (Klaviyo orders include returning buyers)", unit="ratio", num="k_camp_email_orders", den="nc", link="info", account_only=True,
     needs_views=["klaviyo_daily"], gap_text="Klaviyo not in the database yet", gap_where="Klaviyo campaign reports", mat=M(rel=0.15, abs=0.01, min_den=50))
node(id="spend", label="Total spend (Meta + Google)", formula="spend = Meta spend + Google spend (additive across platforms)", unit="usd", num="spend", link="add", children=[_part("meta_spend"), _part("google_spend"), _info("bids")],
     mat=M(rel=0.06, abs=200, min_den=None),
     known_causes=[_kc("2026-10-26", "Bid limits always bound (Meta ROAS ran 0.79-0.92 of the tROAS floors; realised cost per purchase ~30% over the cost caps)", "verified", "supported by control", "down", ["2026-07-12", "2026-10-18"], SYNTH),
                   _kc("2026-10-26", "The Sep 14 tightening of 29 ad sets caused ~98% of the Sep 14 spend cliff (touched ad sets -41%, rest of account -2%)", "verified", "supported by control", "down", ["2026-09-14", "2026-09-16"], SYNTH + " 1.2"),
                   _kc("2026-10-26", "Rodolfo paused 37 ad sets (61% of prior 14-day spend) Oct 1, 2, 5; the buyers' 273 tightenings / 144 loosenings each chased the previous 7 days and reversed", "verified", "consistent with", "down", ["2026-10-01", "2026-10-05"], SYNTH + " 2.2"),
                   _kc("2026-10-26", "Aug 22-29 rebuild started the decline", "refuted", "refuted", "down", ["2026-08-22", "2026-08-29"], SYNTH + " refuted list", "NC ROAS rose 0.47 -> 0.69 after the rebuild"),
                   _kc("2026-09-29", "Loosening caps / floors (Sep 23-24, Oct 2) bought spend with almost no revenue (+12.4% spend, +0.4% revenue)", "verified", "consistent with", "up", ["2026-09-23", "2026-10-02"], "ledger_marketing.md #8")],
     settles_with="the platform split, the Meta mix nodes, and the dated ad-set launches / stops")
node(id="meta_spend", label="Meta spend", formula="Meta spend (platform_daily, Meta ad account days)", unit="usd", num="meta_spend", link="info",
     children=[_info("spend@product"), _info("spend@stage")], mat=M(rel=0.06, abs=150, min_den=None))
node(id="google_spend", label="Google spend", formula="Google spend (google_campaign_daily, Pacific days)", unit="usd", num="google_spend", link="info", account_only=True,
     children=[_info("google_nc_roas@g_type"), _info("google_nc_roas@g_campaign")], mat=M(rel=0.1, abs=40, min_den=None),
     known_causes=[_kc("2026-09-27", "Google Bestseller Shopping killed Aug 5", "verified", "consistent with", "down", ["2026-08-05", None], "ledger_marketing.md #10")])
node(id="bids", label="Bid, cost-cap and budget changes", kind="gap", formula="changes to caps / floors / budgets on ad sets", unit="count",
     data_gap={"why": "Bids, caps, floors and budgets are not in the analytics schema (only a spend-based proxy is: ad sets that stopped, launched, or moved 2x, listed under `changes`).",
               "where": "Ads Manager activity log (history of edits); the Graph API ad set fields; proj_feed_bids (not in analytics)", "follow_up": "expose proj_feed_bids as analytics.meta_bid_changes"},
     known_causes=[_kc("2026-10-26", "Sep 14 tightening of 29 ad sets caused ~98% of that day's spend cliff (like-for-like: touched vs rest of account)", "verified", "supported by control", "down", ["2026-09-14", "2026-09-16"], SYNTH),
                   _kc("2026-10-26", "Tighter caps pushed delivery to women / Reels / international", "refuted", "refuted", None, None, SYNTH + " refuted list"),
                   _kc("2026-10-26", "Tighter floors steer toward returning buyers", "refuted", "refuted", None, None, SYNTH + " refuted list", "returning share 8.5% -> 6.6% after floor raises")])
node(id="nc_roas", label="New-customer ROAS", formula="NC ROAS = new-customer revenue / total spend = NC AOV / NC CPA", unit="ratio", num="nc_rev", den="spend", link="mult", children=[_part("nc_aov", 1), _part("nc_cpa", -1), _info("basis_gap")] +
     [_info(f"nc_roas@{g}") for g in ("product", "stage", "cluster", "icp", "pain_point", "emotional_trigger", "ad_type", "style", "vendor")],
     mat=M(rel=0.06, abs=0.03, min_den=500),
     known_causes=[_kc("2026-10-26", "Spend moved off structures returning 0.6-0.8 NC ROAS onto structures returning 0.3-0.5 (Rodolfo's Oct 1-5 pauses, Oct 20-24 handovers); new ad sets 25-35% worse like-for-like", "verified", "consistent with", "down", ["2026-10-01", "2026-10-24"], SYNTH + " 2.2"),
                   _kc("2026-10-26", "Testing share of spend rose (3.2% -> 10.5%, Oct 11% -> 24%); testing 0.32-0.45 vs scaling ~0.65 drags the blend", "open", "consistent with", "down", ["2026-09-22", "2026-10-18"], "ledger_marketing.md #16"),
                   _kc("2026-10-26", "The September pocket (Aug 31-Sep 27) converted 1.6x normal for ~5 weeks, then reverted to the July rate", "open", "consistent with", None, ["2026-08-31", "2026-09-27"], SYNTH + " 2.3", "needs per-person exposure / a conversion lift study")],
     settles_with="the aov / cpa children, then the mix nodes (product, stage, tags): which values lost ROAS and which gained spend")
node(id="basis_gap", label="Meta-reported revenue per $1 of actual new-customer revenue", formula="Meta own revenue / actual new-customer revenue (Meta counts returning buyers, denominators differ)", unit="ratio", num="meta_own_rev", den="nc_rev", link="info", account_only=True,
     mat=M(rel=0.1, abs=0.05, min_den=1000),
     known_causes=[_kc("2026-10-26", "Meta ROAS 0.705 vs actual 0.586 (Oct 19-24): half denominators (BackUp + Google spend), half Meta counting returning buyers", "verified", "consistent with", "up", ["2026-10-19", "2026-10-24"], SYNTH + " 4"),
                   _kc("2026-10-26", "'API shows no exclusions on the 1DC ad sets'", "refuted", "refuted", None, None, SYNTH + " 4.3", "targeting field hides exclusions on newer-format ad sets; the only gap was the 3 OffWinners1DC ad sets for ~15 hours")])
node(id="nc_aov", label="New-customer AOV", formula="new-customer revenue / new customers", unit="usd", num="nc_rev", den="nc", link="info", children=[_info("nc_units_per_order"), _info("nc_price_per_unit"), _info("nc_discount_per_order")],
     mat=M(rel=0.05, abs=2, min_den=50), known_causes=[_kc("2026-09-30", "Sep ROAS recovery was AOV only; AOV flat Sep -> Oct", "verified", "consistent with", None, ["2026-09-01", "2026-10-31"], "ledger_marketing.md #10")],
     settles_with="bundle mix (units per order) vs price per unit vs discounts")
node(id="nc_units_per_order", label="Units per new-customer order (bundle mix)", formula="units on new-customer orders / new-customer orders (Shopify orders, last 120 days)", unit="ratio", num="so_units", den="so_orders", link="info", account_only=True,
     needs_views=["shopify_orders", "shopify_order_lines"], gap_text="Shopify orders are not in the analytics schema yet", gap_where="Shopify admin / CheckoutChamp order_details (order lines); ExpandFi shopify_orders", mat=M(rel=0.05, abs=0.1, min_den=50))
node(id="nc_price_per_unit", label="Order total per unit on new-customer orders", formula="order totals / units", unit="usd", num="so_rev", den="so_units", link="info", account_only=True,
     needs_views=["shopify_orders", "shopify_order_lines"], gap_text="Shopify orders are not in the analytics schema yet", gap_where="Shopify admin / CheckoutChamp (prices, offers)", mat=M(rel=0.05, abs=1, min_den=50))
node(id="nc_discount_per_order", label="Discount per new-customer order", formula="total discounts / new-customer orders", unit="usd", num="so_disc", den="so_orders", link="info", account_only=True,
     needs_views=["shopify_orders"], gap_text="Shopify orders are not in the analytics schema yet", gap_where="Shopify admin discount codes; CheckoutChamp offers", mat=M(rel=0.1, abs=1, min_den=50))
node(id="nc_cpa", label="New-customer CPA", formula="total spend / new customers = Google factor x cost per outbound click / conversion", unit="usd", num="spend", den="nc", link="mult",
     children=[_part("spend_platform_factor", 1), _part("cpc", 1), _part("conversion", -1)], mat=M(rel=0.06, abs=3, min_den=30),
     known_causes=[_kc("2026-09-30", "CPA rose every month even at lower spend, driven by CPC doubling", "verified", "consistent with", "up", ["2026-04-13", "2026-09-30"], "ledger_marketing.md #10"),
                   _kc("2026-09-29", "NC CPA $76 -> $92 (Apr 13-May 24 vs Aug 31-Sep 27): CPC $1.49 -> $2.74 in every cut", "verified", "consistent with", "up", ["2026-08-31", "2026-09-27"], "model_ops / MEMORY then-vs-now")],
     settles_with="cost per click and conversion children")
node(id="spend_platform_factor", label="Total spend per $1 of Meta spend", formula="total spend / Meta spend (Google's share)", unit="ratio", num="spend", den="meta_spend", link="info", account_only=True, mat=M(rel=0.03, abs=0.02, min_den=500))
node(id="cpc", label="Cost per unique outbound click (Meta)", formula="Meta spend / unique outbound clicks = CPM / (1,000 x unique outbound CTR)", unit="usd", num="meta_spend", den="clicks", link="mult", children=[_part("cpm", 1), _part("ctr", -1)],
     mat=M(rel=0.06, abs=0.05, min_den=1000),
     known_causes=[_kc("2026-10-26", "Cheap clicks: ads with CPC under $1 went from 0.4% to 33% of clicks (relaunched page post ced20 = 16% of Oct 12-18 clicks); BackUp account added 9,122 clicks at ~$0.43", "verified", "consistent with", "down", ["2026-10-05", "2026-10-24"], SYNTH + " 2.1"),
                   _kc("2026-10-26", "A new ad id raises CPC ~36% on the same creative", "open", "untested", "up", ["2026-10-20", "2026-10-24"], SYNTH + " open #9")],
     settles_with="CPM (auction price, mix) and CTR (creative)")
node(id="cpm", label="CPM (Meta cost per 1,000 impressions)", formula="Meta spend / impressions x 1,000", unit="usd", num="meta_spend", den="impressions", scale=1000, link="info",
     children=[_info("frequency"), _info("cpm@product"), _info("cpm@stage"), _info("cpm_country_age_mix")], mat=M(rel=0.06, abs=2, min_den=50000),
     known_causes=[_kc("2026-10-26", "CPM roughly doubled in a mid-August step inside every age cell (US CPM $39 Jul -> $70 Sep; +41% YoY; not seasonal) - cause untested", "open", "untested", "up", ["2026-08-10", "2026-08-31"], SYNTH + " 2.4", "needs CPM by bid strategy by day Aug 10-31 and a Meta auction read"),
                   _kc("2026-10-26", "Country mix explains ~0% of the CPM rise", "verified", "consistent with", "up", None, "ledger_marketing.md #6"),
                   _kc("2026-10-26", "Q4 CPM arriving early", "refuted", "refuted", "up", ["2026-10-01", "2026-10-31"], SYNTH + " refuted list", "CPM fell Sep -> Oct"),
                   _kc("2026-10-18", "Market-wide CPM inflation, not the restructure (Hydrant shows the same pattern)", "verified", "consistent with", "up", None, "ledger_marketing.md #28"),
                   _kc("2026-10-03", "Vita Bull 23% -> 62% of spend (US CPM $77 vs $51-58) and men 45+ share 0.50 -> 0.71 are the top weekly predictors of cost per reach", "verified", "consistent with", "up", ["2026-09-01", "2026-10-02"], "ledger_marketing.md #12 / #32")],
     settles_with="product / stage mix first; then a country x age breakdown in Ads Manager (not in the database)")
node(id="frequency", label="Average daily frequency (impressions / daily reach)", formula="impressions / reach (same-day overlap between ad sets; account level only)", unit="ratio", num="impressions", den="reach_daily", link="info", account_only=True,
     mat=M(rel=0.1, abs=0.2, min_den=50000),
     known_causes=[_kc("2026-10-02", "Frequency is same-day overlap between ad sets, not exposure on more days; audience exhaustion ruled out", "verified", "consistent with", "up", ["2026-09-03", "2026-09-30"], "ledger_marketing.md #12"),
                   _kc("2026-10-26", "More ad sets = self-competition", "refuted", "refuted", "up", None, SYNTH + " refuted list", "overlap fell 2.56 -> 1.87")])
node(id="cpm_country_age_mix", label="CPM by country, age and sex cell; auction price inside a cell", kind="gap", formula="country mix, age / sex mix, price within a cell, overlap between ad sets", unit="usd",
     data_gap={"why": "Reach and cost breakdowns by country, age, sex and placement are not in the analytics schema.", "where": "Ads Manager breakdowns (country, age, gender, placement) / Graph API insights breakdowns",
               "follow_up": "load a daily country x age x gender breakdown into analytics"},
     known_causes=[_kc("2026-10-26", "CPM up in every age cell at once (US 65+ $33.7 -> $73.0) - a market-wide pattern", "verified", "consistent with", "up", ["2026-08-10", "2026-08-31"], SYNTH + " 2.4")])
node(id="ctr", label="Unique outbound CTR", formula="unique outbound clicks / impressions", unit="pct", num="clicks", den="impressions", link="info", mat=M(rel=0.08, abs=0.0005, min_den=50000),
     known_causes=[_kc("2026-10-26", "CTR up while conversion fell: cheap clicks, not lost buyers", "verified", "consistent with", "up", ["2026-10-05", "2026-10-24"], SYNTH + " 2.1")])
node(id="conversion", label="Conversion (new customers per Meta unique outbound click)", formula="actual new customers / unique outbound clicks (account mode); attributed new customers / link clicks (scoped)", unit="pct", num="nc", den="clicks", link="info",
     children=[_info("routing_shift"), _info("conv@family"), _info("conv@product"), _info("conv@stage"), _info("conv@landing_page"), _info("checkout_funnel"), _info("upsell_accepts")], mat=M(rel=0.06, abs=0.001, min_den=1000),
     known_causes=[_kc("2026-10-26", "Same 869 ads lost 23% click->purchase", "refuted", "refuted", "down", None, SYNTH + " refuted list", "spend reweighting across those ads; click-weighted drop is 4%"),
                   _kc("2026-10-26", "'New ads convert worse' as an October deterioration", "refuted", "refuted", "down", None, SYNTH + " refuted list", "new launches converted at the same rate at the same age in September"),
                   _kc("2026-10-26", "Within-product conversion fell vs July (VB -25%, BL -50%, BF -37%); product mix helped slightly", "verified", "consistent with", "down", ["2026-07-01", "2026-10-24"], SYNTH + " 2.3"),
                   _kc("2026-10-26", "September (Aug 31-Sep 27) converted 1.6x normal for ~5 weeks (4.2-4.5% NC per unique click), then reverted", "open", "consistent with", None, ["2026-08-31", "2026-09-27"], SYNTH + " 2.3"),
                   _kc("2026-10-27", "A checkout-form defect from Oct 6", "refuted", "refuted", "down", ["2026-10-06", None], "checkout_split_REPORT.md", "completion 42.4% -> 40.5%, flat on every page / card / PayPal split"),
                   _kc("2026-10-27", "Express-PayPal completes per start halved Oct 5-6", "refuted", "refuted", "down", ["2026-10-05", "2026-10-06"], "checkout_split_REPORT.md", "Josh 2026-10-27: not true; withdrawn")],
     settles_with="ad family mix (and each family's own landing page), product mix, then checkout and upsell in CheckoutChamp")
node(id="checkout_funnel", label="Checkout starts, attempts, completion", kind="gap", formula="starts per click, attempts per start, completes per attempt", unit="ratio",
     data_gap={"why": "Checkout starts and completion are CheckoutChamp / Klaviyo events, not in the analytics schema.", "where": "CheckoutChamp order_details (attempts, completes, PayPal vs card); Klaviyo 'Started Checkout' events",
               "follow_up": "daily checkout funnel view"},
     known_causes=[_kc("2026-10-27", "Checkout start -> order fell 0.330 -> 0.238, but a third was lonvera.com LP orders moving to Shopify 'web'; real fall 0.332 -> 0.261 (-19%) is checkout starts -15% plus upsell accepts", "verified", "consistent with", "down", ["2026-10-06", None], "checkout_split_REPORT.md"),
                   _kc("2026-10-27", "Landing completion 42.4% -> 40.5%: not a checkout fault", "verified", "supported by control", "down", ["2026-09-15", "2026-10-12"], "checkout_split_REPORT.md")])
node(id="upsell_accepts", label="Upsell accepts per checkout start", kind="gap", formula="upsell accepts / checkout starts", unit="ratio",
     data_gap={"why": "Upsell offers and accepts are CheckoutChamp events, not in the analytics schema.", "where": "CheckoutChamp order_details (upsell lines); Klaviyo", "follow_up": "daily upsell funnel view"},
     known_causes=[_kc("2026-10-27", "Upsell accepts per start 10.0% -> 5.6% (counts verified, cause untested)", "open", "untested", "down", ["2026-10-06", None], "checkout_split_REPORT.md")])


node(id="routing_shift", label="Routing shift: the same cluster / ICP / product / family now goes to a different page, stage or campaign", kind="routing", formula="spend distribution of a value over a second dimension, now vs before (total variation >= 30 points) and the new destination converts worse for that value",
     unit="ratio", link="info", mat=M(share=0.3), pq="conv_by_ad",
     known_causes=[_kc("2026-10-26", "Spend moved off structures returning 0.6-0.8 NC ROAS onto structures returning 0.3-0.5 (a routing change between ad sets / campaigns)", "verified", "consistent with", "down", ["2026-10-01", "2026-10-24"], SYNTH + " 2.2")],
     settles_with="the same value on the old destination within the same window, and the new destination for the other values")


def _bdnode(b, rate, parent, label, nid):
    node(id=nid, kind="mix", group="bd_" + b, rate=rate, parent=parent, label=label, link="info", unit="ratio", mat=M(share=0.5), pq=None)


def _mix(group: str, rate: str, parent: str, label: str, nid: str | None = None):
    node(id=nid or f"{parent}@{group}", kind="mix", group=group, rate=rate, parent=parent, label=label, link="info", unit="ratio", mat=M(share=0.5), pq=None)


for g, lab in (("product", "product"), ("stage", "testing vs scaling")):
    _mix(g, "roas", "spend", f"Spend and NC ROAS by {lab}", f"spend@{g}")
for g, lab in (("product", "product"), ("stage", "testing vs scaling"), ("cluster", "market cluster"), ("icp", "ICP"), ("pain_point", "pain point"), ("emotional_trigger", "emotional trigger"), ("ad_type", "creative type"),
               ("style", "creative style"), ("vendor", "vendor")):
    _mix(g, "roas", "nc_roas", f"NC ROAS by {lab}")
for g, lab in (("family", "ad family"), ("product", "product"), ("stage", "testing vs scaling"), ("landing_page", "landing page")):
    _mix(g, "conv", "conversion", f"Conversion by {lab}", f"conv@{g}")
for g, lab in (("product", "product"), ("stage", "testing vs scaling")):
    _mix(g, "cpm", "cpm", f"CPM by {lab}")
for _b in BREAKDOWNS:
    _bdnode(_b, "meta_roas", "meta_spend", f"Meta breakdown by {_b.replace('_', ' ')}: spend share and Meta-reported ROAS", f"bd_roas@{_b}")
for _b in ("audience_segment", "age", "gender", "country", "publisher_platform", "platform_position"):
    _bdnode(_b, "cpm", "cpm", f"CPM by Meta {_b.replace('_', ' ')}", f"bd_cpm@{_b}")
_bdnode("audience_segment", "meta_conv", "conversion", "Meta-reported conversion by audience segment (new audience / engaged / existing customers)", "bd_conv@audience_segment")
_bdnode("audience_segment", "meta_roas", "nc_roas", "Meta-reported ROAS by audience segment vs the Upstack new-customer basis", "bd_roas_nc@audience_segment")
_mix("g_type", "roas", "google_spend", "Google by campaign type", "google_nc_roas@g_type")
_mix("g_campaign", "roas", "google_spend", "Google by campaign", "google_nc_roas@g_campaign")
for n in NODES:
    if n["id"] in ("nc_roas@product",):
        n["known_causes"] = [_kc("2026-10-03", "Vita Bull 23% -> 62% of spend (Sep); within-product conversion fell VB -25%, BL -50%, BF -37% vs July", "verified", "consistent with", "down", ["2026-09-01", "2026-10-24"], SYNTH + " 2.3")]
    if n["id"] == "nc_roas@stage":
        n["known_causes"] = [_kc("2026-10-18", "Testing at 0.32-0.45 vs scaling ~0.65; testing share 11% -> 24% in Oct, blend 0.77 -> 0.68", "open", "consistent with", "down", ["2026-09-22", "2026-10-18"], "ledger_marketing.md #16")]
    if n["id"] == "conv@family":
        n["known_causes"] = [_kc("2026-10-26", "Relaunched page post ced20 alone = 16% of Oct 12-18 clicks at 1.47 NC ROAS: one ad changing the click mix", "verified", "consistent with", "down", ["2026-10-12", "2026-10-18"], SYNTH + " 2.1")]

PQ_MAP = {"bd_roas@audience_segment": "basis_gap", "bd_roas_nc@audience_segment": "basis_gap", "bd_conv@audience_segment": "conversion", "checkout_funnel": "checkout", "upsell_accepts": "upsell", "meta_spend": "spend_by_platform", "google_spend": "spend_by_platform", "repeat_rate": "repeat_orders", "renewals_per_due": "repeat_orders",
          "repeat_cohort_like_for_like": "repeat_orders", "klaviyo_flow_revenue": "repeat_orders", "klaviyo_campaign_revenue": "repeat_orders", "klaviyo_billing_reminders": "repeat_orders",
          "klaviyo_failed_winback": "repeat_orders", "nc_campaign_email_share": "nc_revenue", "spend_platform_factor": "nc_cpa", "nc_units_per_order": "nc_aov", "nc_price_per_unit": "nc_aov",
          "nc_discount_per_order": "nc_aov", "cpm_country_age_mix": "cpm", "spend@product": "spend_by_product", "spend@stage": "spend_by_stage", "nc_roas@product": "conv_by_product",
          "conv@family": "conv_by_ad", "conv@product": "conv_by_product", "conv@stage": "spend_by_stage", "conv@landing_page": "conv_by_ad", "google_nc_roas@g_type": "spend_by_platform",
          "google_nc_roas@g_campaign": "spend_by_platform"}
for _n in NODES:
    _n["pq"] = PQ_MAP.get(_n["id"], _n["pq"] if _n["pq"] in PAST_QUESTIONS else (_n["parent"] if _n.get("parent") in PAST_QUESTIONS else None))
BY_ID = {n["id"]: n for n in NODES}
for _n in NODES:
    if _n["kind"] == "mix" and _n["group"].startswith("bd_") and _n["parent"] in BY_ID:
        BY_ID[_n["parent"]]["children"].append({"id": _n["id"], "role": "info", "sign": 0})
MEASURE_FAMILY = {m: f for f, spec in FAMILIES.items() for m in spec["measures"] if f not in ("sc_meta", "sc_google")}
NOT_IN_DB = [
    {"what": "Klaviyo flows created / edited, flow messages", "where": "Klaviyo API (created / updated), flows page"},
    {"what": "Bid, cost-cap, ROAS-floor and budget edits", "where": "Ads Manager activity log; proj_feed_bids (not in analytics)"},
    {"what": "Price, offer, checkout / CheckoutChamp configuration, subscription intervals", "where": "Shopify admin, CheckoutChamp, Skio"},
    {"what": "Pixel / tracking / attribution-setting changes", "where": "Events Manager, Ads Manager attribution setting"},
    {"what": "Country, age, gender, placement breakdowns", "where": "Ads Manager breakdowns"},
]


def tree_definition(include_sql: bool = False) -> dict:
    nodes = []
    for n in NODES:
        d = {k: v for k, v in n.items() if k not in ("mat", "pq")}
        d["materiality"] = n.get("mat")
        d["past_questions"] = PAST_QUESTIONS.get(n.get("pq"), [])
        fams = sorted({MEASURE_FAMILY[m] for m in _measures_of(n) if m in MEASURE_FAMILY})
        d["sql_families"] = fams
        if n["kind"] == "mix":
            d["sql_group"] = n["group"]
        if n["kind"] == "gap" or n.get("needs_views"):
            d["data_gap"] = n.get("data_gap") or {"why": n.get("gap_text"), "where": n.get("gap_where"), "runs_when_views_exist": n.get("needs_views")}
        nodes.append(d)
    out = {"root": "revenue", "arrows": "children read 'of which' (accounting identity, not cause); 'part' children are exact algebraic parts (add or mult), 'info' children explain or break down",
           "grains": ["day (last 35 days)", "week (last 13 Monday weeks)", "month (6 closed months + month-to-date vs the same days of earlier months)"],
           "concrete_cause": ["(a) a dated change event within 3 days of the break date", "(b) one entity (ad family, ad set, product, tag value, page, flow) that accounts for at least 50% of the parent's change",
                              "(c) a market-wide pattern (every cell moved the same way)"], "controls": ["counterfactual exclusion (recompute the parent without the entity)", "pre/post with a flat pre-trend check", "like-for-like on the values that did not change"],
           "nodes": nodes, "change_sources": {k: {"needs_views": v["needs"], "where": v["where"]} for k, v in CHANGE_SQL.items()}, "not_in_database": NOT_IN_DB}
    if include_sql:
        out["sql"] = {"families": {k: v["sql"].strip() for k, v in FAMILIES.items()}, "mix_groups": {g: group_sql(g).strip() for g in MIX_GROUPS}, "changes": {k: v["sql"].strip() for k, v in CHANGE_SQL.items()}}
    return out


def _measures_of(n: dict) -> set[str]:
    import re
    return set(re.findall(r"[a-z_]+", " ".join(str(n.get(k) or "") for k in ("num", "den"))))
